"""C9 — unexplained change (spec §3 case C9, step-13).

Trigger: a changed non-test function or method where EITHER
  (a) every commit that modified it has no work-item key in its message, OR
  (b) the entity is not in top-5 semantic candidates for any criterion of
      any requirement the PR links to.

Severity: review (configurable).

Pure. Reads only the bundle + the pre-computed TraceLinks (Branch 5
`evaluate/links.py` produces them).
"""

from __future__ import annotations

from codeatlas.schema import (
    CaseId,
    ChangedEntity,
    CheckResult,
    EvidenceBundle,
    Policy,
    TraceLink,
    WorkItemRef,
)

CASE: CaseId = "C9"
VERSION = "1"
REQUIRED_EVIDENCE = ("changed", "commits", "work_items", "candidates")

_TEST_PATH_PATTERNS = ("tests/", "test_", "_test.py")


class C9UnexplainedChange:
    case: CaseId = CASE
    version: str = VERSION
    required_evidence: tuple[str, ...] = REQUIRED_EVIDENCE

    def check(
        self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy
    ) -> CheckResult:
        severity = _severity_for(policy)

        # Entities we care about: changed functions/methods that AREN'T in tests.
        non_test_changes = [
            ce for ce in bundle.changed
            if ce.entity_id is not None and not _is_test_path(ce.file)
        ]
        if not non_test_changes:
            return CheckResult(
                case=CASE, outcome="NOT_APPLICABLE", severity="none", required=False,
                message="No non-test code entities changed in this PR.",
                evidence_ids=[],
            )

        # Index: for each changed entity, which commits touched it + which keys they carry.
        commit_keys = _commit_key_index(bundle.work_items)
        req_top5 = _entity_in_top5_index(bundle)

        unexplained: list[tuple[str, str, str]] = []  # (entity_id, file, reason)
        for ce in non_test_changes:
            assert ce.entity_id is not None
            # (a) only modified by keyless commits
            keys_in_touching_commits: set[str] = set()
            for sha in ce.commit_shas:
                keys_in_touching_commits.update(commit_keys.get(sha, set()))
            if not keys_in_touching_commits:
                unexplained.append((
                    ce.entity_id, ce.file,
                    f"all {len(ce.commit_shas)} commit(s) touching entity had no [A-Z]+-\\d+ key",
                ))
                continue

            # (b) not in top-5 semantic candidates for any of those keys' requirements
            matches_any_top5 = any(
                ce.entity_id in req_top5.get(key, set())
                for key in keys_in_touching_commits
            )
            if not matches_any_top5:
                unexplained.append((
                    ce.entity_id, ce.file,
                    f"entity not in top-5 semantic candidates for any criterion of {sorted(keys_in_touching_commits)}",
                ))

        if not unexplained:
            return CheckResult(
                case=CASE, outcome="SATISFIED", severity="none", required=False,
                message=f"All {len(non_test_changes)} changed non-test entities are explained by their commit's ticket or by semantic candidates.",
                evidence_ids=[],
            )

        preview = ", ".join(f"{eid} ({reason[:40]}…)" for eid, _, reason in unexplained[:3])
        return CheckResult(
            case=CASE, outcome="VIOLATED", severity=severity, required=False,
            message=(
                f"{len(unexplained)} changed entity(ies) with no linked requirement: {preview}"
                + (f" (+{len(unexplained) - 3} more)" if len(unexplained) > 3 else "")
            ),
            evidence_ids=sorted({
                ce.evidence_id for ce in non_test_changes if ce.entity_id in {u[0] for u in unexplained}
            }),
        )


def _severity_for(policy: Policy) -> str:
    case_policy = policy.cases.get("C9")
    if case_policy and case_policy.severity:
        return case_policy.severity
    return "review"


def _is_test_path(path: str) -> bool:
    lower = path.lower()
    return any(p in lower for p in _TEST_PATH_PATTERNS)


def _commit_key_index(refs: list[WorkItemRef]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for ref in refs:
        if ref.commit_sha:
            out.setdefault(ref.commit_sha, set()).add(ref.key)
    return out


def _entity_in_top5_index(bundle: EvidenceBundle) -> dict[str, set[str]]:
    """For each requirement key, the set of entity_ids that are top-5 for one of its criteria."""
    out: dict[str, set[str]] = {}
    for cand in bundle.candidates:
        if cand.fused_rank > 5:
            continue
        req_key = cand.criterion_id.split(":")[0]
        out.setdefault(req_key, set()).add(cand.entity_id)
    return out
