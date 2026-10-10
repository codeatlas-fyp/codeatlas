"""Tests for `evaluate.links.assign_link_states` — first-match-wins ordering."""

from __future__ import annotations

from datetime import UTC, datetime

from codeatlas.evaluate.links import assign_link_states
from codeatlas.schema import (
    ChangedEntity,
    CodeEdge,
    Commit,
    GovernanceSnapshot,
    SemanticCandidate,
    WorkItemRef,
)

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _changed(entity_id: str | None, file: str, commit_shas: list[str]) -> ChangedEntity:
    return ChangedEntity(
        evidence_id=f"changed:{entity_id or file}", source="analysis", source_ref=f"diff:{file}",
        retrieved_at=NOW, extractor_version="code@0.1.0",
        entity_id=entity_id, change="modified",
        file=file, hunk_lines=(1, 10), commit_shas=commit_shas,
    )


def _commit(sha: str, message: str) -> Commit:
    return Commit(
        evidence_id=f"commit:{sha}", source="github", source_ref=f"commit:{sha}",
        retrieved_at=NOW, extractor_version="github@0.1.0",
        sha=sha, author_email="a@b.test", author_login="a",
        committed_at=NOW, message=message,
    )


def _ref(key: str, sha: str) -> WorkItemRef:
    return WorkItemRef(
        evidence_id=f"keys:commit:{sha}:{key}", source="analysis",
        source_ref=f"commit:{sha}", retrieved_at=NOW, extractor_version="keys@0.1.0",
        key=key, found_in="commit", commit_sha=sha,
    )


def _edge(src: str, dst: str) -> CodeEdge:
    return CodeEdge(src=src, dst=dst, kind="IMPORTS", confident=True)


def _empty_governance() -> GovernanceSnapshot:
    return GovernanceSnapshot(
        evidence_id="governance:base", source="analysis", source_ref="base@base",
        retrieved_at=NOW, extractor_version="governance@0.1.0",
        base_sha="base", codeowners_path=None, owner_rules=[], adrs=[],
        policy_blob_sha=None,
    )


def _cand(crit: str, entity: str, rank: int) -> SemanticCandidate:
    return SemanticCandidate(
        criterion_id=crit, entity_id=entity,
        bm25_rank=rank, dense_rank=rank,
        fused_rank=rank, fused_score=1.0 / (60 + rank),
        model_id="test@v1",
    )


def test_observed_state_when_commit_message_carries_key() -> None:
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[_changed("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "feat(fees): calc (SBX-4)")],
        work_item_refs=[_ref("SBX-4", "sha1")],
        edges=[], governance=_empty_governance(),
        candidates=[],
    )
    assert len(links) == 1
    assert links[0].state == "OBSERVED"


def test_deterministically_derived_via_import_edge() -> None:
    """fees.format_receipt isn't directly committed as SBX-4, but imports fees.calc."""
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[
            _changed("pkg.fees.calc", "pkg/fees.py", ["sha1"]),           # OBSERVED
            _changed("pkg.other.helper", "pkg/other.py", ["sha2"]),       # should derive
        ],
        commits=[_commit("sha1", "SBX-4"), _commit("sha2", "cleanup")],
        work_item_refs=[_ref("SBX-4", "sha1")],
        edges=[_edge("pkg.other", "pkg.fees")],  # other imports fees
        governance=_empty_governance(),
        candidates=[],
    )
    helper_link = next(l for l in links if l.entity_id == "pkg.other.helper")
    assert helper_link.state == "DETERMINISTICALLY_DERIVED"


def test_semantic_candidate_when_in_top5_but_not_observed_or_derived() -> None:
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[_changed("pkg.fees.calc", "pkg/fees.py", ["sha2"])],  # no SBX-4 in commit
        commits=[_commit("sha2", "generic cleanup")],
        work_item_refs=[],  # no refs link sha2 to SBX-4
        edges=[], governance=_empty_governance(),
        candidates=[_cand("SBX-4:v1:ac1", "pkg.fees.calc", rank=2)],
    )
    assert links[0].state == "SEMANTIC_CANDIDATE"


def test_unresolved_when_nothing_matches() -> None:
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[_changed("pkg.fees.calc", "pkg/fees.py", ["sha2"])],
        commits=[_commit("sha2", "generic")],
        work_item_refs=[],
        edges=[], governance=_empty_governance(),
        candidates=[],
    )
    assert links[0].state == "UNRESOLVED"


def test_observed_wins_over_semantic() -> None:
    """Entity could be semantic candidate AND observed. Observed wins (earlier in order)."""
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[_changed("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_item_refs=[_ref("SBX-4", "sha1")],
        edges=[], governance=_empty_governance(),
        candidates=[_cand("SBX-4:v1:ac1", "pkg.fees.calc", rank=1)],
    )
    assert links[0].state == "OBSERVED"


def test_unresolved_mapping_entities_are_skipped() -> None:
    """ChangedEntity with entity_id=None has no analysis to run against."""
    links = assign_link_states(
        requirement_keys=["SBX-4"],
        changed=[_changed(None, "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_item_refs=[_ref("SBX-4", "sha1")],
        edges=[], governance=_empty_governance(),
        candidates=[],
    )
    assert links == []


def test_multiple_requirements_each_get_their_own_link() -> None:
    """Each (req_key, entity) pair gets its own TraceLink."""
    links = assign_link_states(
        requirement_keys=["SBX-4", "SBX-5"],
        changed=[_changed("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_item_refs=[_ref("SBX-4", "sha1")],
        edges=[], governance=_empty_governance(),
        candidates=[],
    )
    # Two links: SBX-4 → OBSERVED, SBX-5 → UNRESOLVED
    assert len(links) == 2
    sbx4 = next(l for l in links if l.requirement_key == "SBX-4")
    sbx5 = next(l for l in links if l.requirement_key == "SBX-5")
    assert sbx4.state == "OBSERVED"
    assert sbx5.state == "UNRESOLVED"
