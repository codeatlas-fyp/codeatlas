"""Link-state assignment (spec §7.3, step-13).

For each (requirement_key, changed_entity) pair we have, assign exactly one
state. First match wins — order matters:

    1. CONFLICTING                — entity's commits carry a DIFFERENT key
       than the requirement being traced, AND entity is top-1 semantic
       candidate only for the other key's criteria. "The commit author
       thought this was for another ticket."

    2. OBSERVED                   — a commit touching this entity carries
       the requirement's key in its message. Strongest signal: the person
       who wrote the code labelled it with this key.

    3. DETERMINISTICALLY_DERIVED  — entity is reachable from an OBSERVED
       entity via one confident IMPORTS edge (Branch 4), OR the entity's
       file lies inside the `scope` of an ADR that lists the requirement's
       key in its `requirement_keys` (Branch 3's governance snapshot).

    4. SEMANTIC_CANDIDATE          — entity is in top-5 for at least one of
       the requirement's criteria.

    5. UNRESOLVED                  — nothing above matched.

Pure function. All inputs come from the bundle; no I/O.
"""

from __future__ import annotations

from codeatlas.schema import (
    ChangedEntity,
    CodeEdge,
    Commit,
    GovernanceSnapshot,
    LinkState,
    SemanticCandidate,
    TraceLink,
    WorkItemRef,
)


def assign_link_states(
    *,
    requirement_keys: list[str],
    changed: list[ChangedEntity],
    commits: list[Commit],
    work_item_refs: list[WorkItemRef],
    edges: list[CodeEdge],
    governance: GovernanceSnapshot,
    candidates: list[SemanticCandidate],
) -> list[TraceLink]:
    """Return one TraceLink per (requirement_key, changed_entity) pair.

    Entities with `entity_id=None` (unresolved mapping from Branch 4) are
    skipped — nothing semantic can be said about them, and C9 handles them
    separately by file path.
    """
    # Build fast lookups once.
    commit_to_keys = _commit_key_index(work_item_refs)
    entity_to_commits = _entity_commit_index(changed)
    import_reverse: dict[str, set[str]] = {}
    for edge in edges:
        if edge.kind == "IMPORTS" and edge.confident:
            import_reverse.setdefault(edge.dst, set()).add(edge.src)
    adr_scope_by_key = _adr_scope_index(governance)
    candidates_by_criterion: dict[str, list[SemanticCandidate]] = {}
    for cand in candidates:
        candidates_by_criterion.setdefault(cand.criterion_id, []).append(cand)
    for cands in candidates_by_criterion.values():
        cands.sort(key=lambda c: c.fused_rank)

    changed_entities = [c for c in changed if c.entity_id is not None]
    links: list[TraceLink] = []

    for req_key in requirement_keys:
        req_criterion_prefix = f"{req_key}:"
        req_criteria_candidates: list[SemanticCandidate] = [
            cand
            for crit_id, cands in candidates_by_criterion.items()
            if crit_id.startswith(req_criterion_prefix)
            for cand in cands
        ]
        req_top5_entities = {c.entity_id for c in req_criteria_candidates if c.fused_rank <= 5}
        req_top1_entities = {c.entity_id for c in req_criteria_candidates if c.fused_rank == 1}

        observed_entities: set[str] = set()
        observed_evidence: dict[str, list[str]] = {}
        for ce in changed_entities:
            commits_touching = entity_to_commits.get(ce.entity_id, [])
            for sha in commits_touching:
                keys_in_commit = commit_to_keys.get(sha, set())
                if req_key in keys_in_commit:
                    observed_entities.add(ce.entity_id)  # type: ignore[arg-type]
                    observed_evidence.setdefault(ce.entity_id, []).append(f"commit:{sha}")  # type: ignore[arg-type]

        # DETERMINISTICALLY_DERIVED: resolve module-level import edges back to entities.
        observed_modules = {_module_of(entity_id) for entity_id in observed_entities}
        reachable_modules: set[str] = set()
        for module in observed_modules:
            reachable_modules.update(import_reverse.get(module, set()))
            for edge in edges:
                if edge.src == module and edge.kind == "IMPORTS" and edge.confident:
                    reachable_modules.add(edge.dst)
        reachable = {
            ce.entity_id for ce in changed_entities
            if ce.entity_id is not None and _module_of(ce.entity_id) in reachable_modules
        }

        adr_scope_files = adr_scope_by_key.get(req_key, [])

        for ce in changed_entities:
            entity_id = ce.entity_id
            assert entity_id is not None

            # 1. CONFLICTING
            commits_touching = entity_to_commits.get(entity_id, [])
            carried_keys: set[str] = set()
            for sha in commits_touching:
                carried_keys.update(commit_to_keys.get(sha, set()))
            other_keys = carried_keys - {req_key}
            is_top1_only_for_others = (
                entity_id not in req_top1_entities
                and any(
                    cand.entity_id == entity_id and cand.fused_rank == 1
                    and cand.criterion_id.split(":")[0] in other_keys
                    for cands in candidates_by_criterion.values()
                    for cand in cands
                )
            )
            if other_keys and is_top1_only_for_others and req_key not in carried_keys:
                links.append(_link(
                    req_key=req_key, entity_id=entity_id, state="CONFLICTING",
                    evidence_ids=[f"commit:{s}" for s in commits_touching] + [ce.evidence_id],
                    reasons=[
                        f"commits touching entity carry {sorted(other_keys)}, not {req_key}",
                        f"entity is top-1 candidate only for {sorted(other_keys)}",
                    ],
                ))
                continue

            # 2. OBSERVED
            if entity_id in observed_entities:
                links.append(_link(
                    req_key=req_key, entity_id=entity_id, state="OBSERVED",
                    evidence_ids=observed_evidence[entity_id] + [ce.evidence_id],
                    reasons=[f"commit(s) touching entity carry {req_key} in message"],
                ))
                continue

            # 3. DETERMINISTICALLY_DERIVED (import reachability or ADR scope)
            file_matches_adr = any(_file_matches(ce.file, pat) for pat in adr_scope_files)
            if entity_id in reachable or file_matches_adr:
                reasons: list[str] = []
                if entity_id in reachable:
                    reasons.append("reachable from OBSERVED entity via one confident IMPORTS edge")
                if file_matches_adr:
                    reasons.append(f"file lies inside ADR scope for {req_key}")
                links.append(_link(
                    req_key=req_key, entity_id=entity_id, state="DETERMINISTICALLY_DERIVED",
                    evidence_ids=[ce.evidence_id],
                    reasons=reasons,
                ))
                continue

            # 4. SEMANTIC_CANDIDATE
            if entity_id in req_top5_entities:
                best = min(
                    (c for c in req_criteria_candidates if c.entity_id == entity_id),
                    key=lambda c: c.fused_rank,
                    default=None,
                )
                reasons = [f"top-{best.fused_rank} semantic candidate for criterion {best.criterion_id}"] if best else []
                links.append(_link(
                    req_key=req_key, entity_id=entity_id, state="SEMANTIC_CANDIDATE",
                    evidence_ids=[ce.evidence_id],
                    reasons=reasons,
                    criterion_id=best.criterion_id if best else None,
                ))
                continue

            # 5. UNRESOLVED
            links.append(_link(
                req_key=req_key, entity_id=entity_id, state="UNRESOLVED",
                evidence_ids=[ce.evidence_id],
                reasons=["no commit, no derivation, no semantic match in top-5"],
            ))

    return links


def _link(
    *,
    req_key: str,
    entity_id: str,
    state: LinkState,
    evidence_ids: list[str],
    reasons: list[str],
    criterion_id: str | None = None,
) -> TraceLink:
    return TraceLink(
        requirement_key=req_key,
        criterion_id=criterion_id,
        entity_id=entity_id,
        state=state,
        evidence_ids=sorted(set(evidence_ids)),
        reasons=reasons,
    )


def _commit_key_index(refs: list[WorkItemRef]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for ref in refs:
        if ref.commit_sha:
            out.setdefault(ref.commit_sha, set()).add(ref.key)
    return out


def _entity_commit_index(changed: list[ChangedEntity]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for ce in changed:
        if ce.entity_id is None:
            continue
        for sha in ce.commit_shas:
            out.setdefault(ce.entity_id, []).append(sha)
    return out


def _adr_scope_index(governance: GovernanceSnapshot) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for adr in governance.adrs:
        for key in adr.requirement_keys:
            out.setdefault(key, []).extend(adr.scope)
    return out


def _file_matches(file_path: str, pattern: str) -> bool:
    """Simple glob with `**` support (same shape as governance._glob_match_double_star)."""
    import re
    if pattern == "*":
        return True
    parts = pattern.lstrip("/").split("/")
    regex_parts: list[str] = []
    for part in parts:
        if part == "**":
            regex_parts.append(".*")
        else:
            escaped = re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            regex_parts.append(escaped)
    regex = "^" + "/".join(regex_parts) + "$"
    return re.match(regex, file_path) is not None


def _module_of(entity_id: str) -> str:
    """Strip the trailing name and class segment to get an entity's module."""
    parts = entity_id.split(".")
    if len(parts) <= 1:
        return entity_id
    parts = parts[:-1]
    if parts and parts[-1][:1].isupper():
        parts = parts[:-1]
    return ".".join(parts) if parts else entity_id
