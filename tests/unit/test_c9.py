"""Tests for C9 (unexplained change)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import BundleStore
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.c9_unexplained_change import C9Advisory, C9UnexplainedChange
from codeatlas.evaluate.verdict import decide
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    CasePolicy,
    ChangedEntity,
    ChangeRef,
    CheckResult,
    Commit,
    EvidenceBundle,
    GovernanceSnapshot,
    IdentityMap,
    Policy,
    SemanticCandidate,
    SemanticPolicy,
    WorkItemRef,
)
from tests.samples import SEMANTIC_RUN

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _policy() -> Policy:
    return Policy(
        version=1,
        workitem_key_pattern=r"[A-Z]+-\d+",
        requirement_fields=["summary", "description", "acceptance_criteria"],
        governance_paths=[],
        approval=ApprovalPolicy(status="Approved", approver_account_ids=["acct-1"]),
        priority_authority=AuthorityPolicy(account_ids=["acct-1"]),
        cases={},
        semantic=SemanticPolicy(top_k=5),
    )


def _empty_gov() -> GovernanceSnapshot:
    return GovernanceSnapshot(
        evidence_id="governance:base",
        source="analysis",
        source_ref="base@base",
        retrieved_at=NOW,
        extractor_version="governance@0.1.0",
        base_sha="base",
        codeowners_path=None,
        owner_rules=[],
        adrs=[],
        policy_blob_sha=None,
    )


def _bundle(
    *,
    changed: list[ChangedEntity],
    commits: list[Commit],
    work_items: list[WorkItemRef],
    candidates: list[SemanticCandidate],
) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="0",
        change=ChangeRef(
            repo="x/y", pr_number=1, base_sha="base", head_sha="head", merge_base_sha="base"
        ),
        codeatlas_version="0.1.0",
        collected_at=NOW,
        run_id="r",
        extractor_versions={},
        model_ids=[],
        model_file_hashes={},
        policy=_policy(),
        identity_map=IdentityMap(people=[]),
        work_items=work_items,
        change_events=[],
        versions=[],
        lifecycle=[],
        commits=commits,
        reviews=[],
        people=[],
        governance=_empty_gov(),
        governance_changes=[],
        entities=[],
        changed=changed,
        edges=[],
        criteria=[],
        candidates=candidates,
        collection_notes=[],
    )


def _ce(entity_id: str | None, file: str, commit_shas: list[str]) -> ChangedEntity:
    return ChangedEntity(
        evidence_id=f"changed:{entity_id or file}",
        source="analysis",
        source_ref=f"diff:{file}",
        retrieved_at=NOW,
        extractor_version="code@0.1.0",
        entity_id=entity_id,
        change="modified",
        file=file,
        hunk_lines=(1, 10),
        commit_shas=commit_shas,
    )


def _commit(sha: str, msg: str) -> Commit:
    return Commit(
        evidence_id=f"commit:{sha}",
        source="github",
        source_ref=f"commit:{sha}",
        retrieved_at=NOW,
        extractor_version="github@0.1.0",
        sha=sha,
        author_email="a@b.test",
        author_login="a",
        committed_at=NOW,
        message=msg,
    )


def _ref(key: str, sha: str) -> WorkItemRef:
    return WorkItemRef(
        evidence_id=f"keys:commit:{sha}:{key}",
        source="analysis",
        source_ref=f"commit:{sha}",
        retrieved_at=NOW,
        extractor_version="keys@0.1.0",
        key=key,
        found_in="commit",
        commit_sha=sha,
    )


def _cand(crit: str, entity: str, rank: int) -> SemanticCandidate:
    return SemanticCandidate(
        criterion_id=crit,
        entity_id=entity,
        bm25_rank=rank,
        dense_rank=rank,
        fused_rank=rank,
        fused_score=1.0 / (60 + rank),
        model_id="test@v1",
    )


def test_satisfied_when_entity_commit_carries_a_key() -> None:
    rule = C9UnexplainedChange()
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[_cand("SBX-4:v1:ac1", "pkg.fees.calc", rank=1)],
    )
    assert rule.check(bundle, [], bundle.policy).outcome == "SATISFIED"


def test_violated_when_only_commits_are_keyless() -> None:
    rule = C9UnexplainedChange()
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "cleanup, no key here")],
        work_items=[],  # no keys found
        candidates=[],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert result.severity == "review"
    assert "pkg.fees.calc" in result.message


def test_violated_when_entity_not_in_top5_for_its_key() -> None:
    """SBX-9 scenario: commit says SBX-4 but the function has nothing to do with it."""
    rule = C9UnexplainedChange()
    bundle = _bundle(
        changed=[_ce("pkg.other.format_receipt", "pkg/other.py", ["sha1"])],
        commits=[_commit("sha1", "feat: format receipts (SBX-4)")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[
            _cand("SBX-4:v1:ac1", "pkg.fees.calc", rank=1),  # SBX-4's top candidate is elsewhere
            _cand("SBX-4:v1:ac1", "pkg.fees.waive", rank=2),
        ],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert "top-5" in result.message


def test_zero_evidence_candidate_does_not_explain_changed_entity() -> None:
    rule = C9UnexplainedChange()
    no_match = SemanticCandidate(
        criterion_id="SBX-4:v1:ac1",
        entity_id="pkg.other.format_receipt",
        bm25_rank=None,
        dense_rank=None,
        fused_rank=1,
        fused_score=0.0,
        model_id="test@bm25-only",
    )
    bundle = _bundle(
        changed=[_ce("pkg.other.format_receipt", "pkg/other.py", ["sha1"])],
        commits=[_commit("sha1", "feat: format receipts (SBX-4)")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[no_match],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "VIOLATED"
    assert "top-5" in result.message


def test_missing_semantic_candidate_is_review_not_functional_failure() -> None:
    bundle = _bundle(
        changed=[_ce("pkg.other.format_receipt", "pkg/other.py", ["sha1"])],
        commits=[_commit("sha1", "feat: format receipts (SBX-4)")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[],
    )
    raw_policy = bundle.policy.model_dump()
    raw_policy["cases"]["C9"] = {"severity": "review"}
    policy = Policy.model_validate(raw_policy)
    result = C9UnexplainedChange().check(bundle, [], policy)
    assert result.severity == "review"
    assert decide([result]) == "REVIEW"


def test_test_files_are_excluded() -> None:
    rule = C9UnexplainedChange()
    bundle = _bundle(
        changed=[_ce("pkg.test_fees.test_calc", "tests/test_fees.py", ["sha1"])],
        commits=[_commit("sha1", "no key")],
        work_items=[],
        candidates=[],
    )
    # Only test files changed → NOT_APPLICABLE, not VIOLATED
    assert rule.check(bundle, [], bundle.policy).outcome == "NOT_APPLICABLE"


def test_unresolved_entities_are_ignored() -> None:
    rule = C9UnexplainedChange()
    bundle = _bundle(
        changed=[_ce(None, "pkg/anything.py", ["sha1"])],
        commits=[_commit("sha1", "no key")],
        work_items=[],
        candidates=[],
    )
    assert rule.check(bundle, [], bundle.policy).outcome == "NOT_APPLICABLE"


def test_message_shows_up_to_three_entities_then_count() -> None:
    rule = C9UnexplainedChange()
    changed = [_ce(f"pkg.m.fn{i}", f"pkg/m{i}.py", [f"sha{i}"]) for i in range(6)]
    commits = [_commit(f"sha{i}", "no key") for i in range(6)]
    bundle = _bundle(changed=changed, commits=commits, work_items=[], candidates=[])
    result = rule.check(bundle, [], bundle.policy)
    assert "+3 more" in result.message


def test_abstained_candidate_does_not_explain_changed_entity() -> None:
    """A top-1 candidate the CA-02 gate abstained on is "no confident match": C9 still fires."""
    rule = C9UnexplainedChange()
    abstained = _cand("SBX-4:v1:ac1", "pkg.fees.calc", rank=1).model_copy(
        update={"gate": "abstain"}
    )
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "feat: fees (SBX-4)")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[abstained],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"


def test_codet5_rank_alone_counts_as_evidence() -> None:
    """With MiniLM off and no BM25 hit, a CodeT5+ rank still explains the entity."""
    rule = C9UnexplainedChange()
    only_codet5 = SemanticCandidate(
        criterion_id="SBX-4:v1:ac1",
        entity_id="pkg.fees.calc",
        bm25_rank=None,
        dense_rank=None,
        codet5_rank=1,
        fused_rank=1,
        fused_score=round(1 / 61, 4),
        model_id="test@codet5p",
        gate="review",
    )
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "feat: fees (SBX-4)")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[only_codet5],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "SATISFIED"


@pytest.mark.parametrize("candidate_state", ["missing", "unavailable", "abstained"])
def test_current_c9_cannot_turn_semantic_uncertainty_into_block(candidate_state: str) -> None:
    candidates = []
    if candidate_state == "unavailable":
        candidates = [_cand("SBX-4:v1:ac1", "pkg.fees.calc", 1).model_copy(
            update={"bm25_rank": None, "dense_rank": None}
        )]
    elif candidate_state == "abstained":
        candidates = [_cand("SBX-4:v1:ac1", "pkg.fees.calc", 1).model_copy(
            update={"gate": "abstain"}
        )]
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=candidates,
    )
    # model_copy bypasses validation: exercise the rule's defensive boundary too.
    policy = bundle.policy.model_copy(update={"cases": {"C9": CasePolicy(severity="block")}})
    result = C9Advisory().check(bundle, [], policy)
    assert (result.outcome, result.severity, decide([result])) == ("VIOLATED", "review", "REVIEW")
    assert "advisory" in result.message
    governance = CheckResult(
        case="C5", outcome="VIOLATED", severity="block", required=True,
        message="owner did not approve", evidence_ids=[],
    )
    assert decide([result, governance]) == "FAIL"


def test_blocking_c9_policy_is_rejected_and_legacy_rule_versions_remain_available() -> None:
    raw = _policy().model_dump()
    raw["cases"] = {"C9": {"severity": "block"}}
    with pytest.raises(ValidationError, match="C9 is advisory"):
        Policy.model_validate(raw)
    assert "C9@1" in ruleset_version(default_rules())
    assert "C9@2" in ruleset_version(default_rules(semantic_run=True))
    assert "C9@3" in ruleset_version(default_rules(advisory_c9=True))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_c9_version_is_persisted_and_replays(version: int, tmp_path: Path) -> None:
    bundle = _bundle(
        changed=[_ce("pkg.fees.calc", "pkg/fees.py", ["sha1"])],
        commits=[_commit("sha1", "SBX-4")],
        work_items=[_ref("SBX-4", "sha1")],
        candidates=[],
    )
    if version >= 2:
        bundle = bundle.model_copy(update={"semantic_run": SEMANTIC_RUN})
    if version == 3:
        bundle = bundle.model_copy(update={"c9_rule_version": "3"})
    store = BundleStore(tmp_path)
    digest = store.save(bundle)
    verdict = evaluate_bundle(bundle, digest)
    assert f"C9@{version}" in verdict.ruleset_version
    store.save_verdict(verdict)
    assert replay(store.folder(digest), evaluate_bundle).match
