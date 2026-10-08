"""Tests for C5 (code owner did not approve the head commit)."""

from __future__ import annotations

from datetime import UTC, datetime

from codeatlas.analyze.governance import parse_codeowners
from codeatlas.evaluate.rules.c5_owner_did_not_approve import C5OwnerDidNotApprove
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    ChangedEntity,
    ChangeRef,
    Commit,
    EvidenceBundle,
    GovernanceSnapshot,
    IdentityMap,
    Policy,
    Review,
    SemanticPolicy,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


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


def _governance(codeowners_text: str | None = None) -> GovernanceSnapshot:
    rules = parse_codeowners(codeowners_text) if codeowners_text else []
    return GovernanceSnapshot(
        evidence_id="governance:base",
        source="analysis", source_ref="base@base",
        retrieved_at=NOW, extractor_version="governance@0.1.0",
        base_sha="base",
        codeowners_path="CODEOWNERS" if codeowners_text else None,
        owner_rules=rules,
        adrs=[], policy_blob_sha=None,
    )


def _commit(sha: str, login: str | None) -> Commit:
    return Commit(
        evidence_id=f"commit:{sha}",
        source="github", source_ref=f"commit:{sha}",
        retrieved_at=NOW, extractor_version="github@0.1.0",
        sha=sha, author_email=f"{login or 'anon'}@example.test",
        author_login=login, committed_at=NOW, message="work",
    )


def _review(reviewer: str, state: str, commit_id: str) -> Review:
    return Review(
        evidence_id=f"review:{reviewer}:{commit_id}",
        source="github", source_ref=f"review:{reviewer}:{commit_id}",
        retrieved_at=NOW, extractor_version="github@0.1.0",
        reviewer_login=reviewer, state=state,  # type: ignore[arg-type]
        commit_id=commit_id, submitted_at=NOW,
    )


def _changed(file: str) -> ChangedEntity:
    return ChangedEntity(
        evidence_id=f"changed:{file}",
        source="analysis", source_ref=f"diff:{file}",
        retrieved_at=NOW, extractor_version="code@0.1.0",
        entity_id=None, change="modified",
        file=file, hunk_lines=(1, 5), commit_shas=["head"],
    )


def _bundle(
    *,
    governance: GovernanceSnapshot,
    reviews: list[Review],
    commits: list[Commit],
    changed: list[ChangedEntity],
    head_sha: str = "head",
) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="0",
        change=ChangeRef(repo="x/y", pr_number=1, base_sha="base",
                        head_sha=head_sha, merge_base_sha="base"),
        codeatlas_version="0.1.0",
        collected_at=NOW, run_id="r",
        extractor_versions={}, model_ids=[], model_file_hashes={},
        policy=_policy(), identity_map=IdentityMap(people=[]),
        work_items=[], change_events=[], versions=[], lifecycle=[],
        commits=commits, reviews=reviews, people=[],
        governance=governance, governance_changes=[],
        entities=[], changed=changed, edges=[],
        criteria=[], candidates=[],
        collection_notes=[],
    )


def test_not_applicable_when_no_codeowners_at_base() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance(None),
        reviews=[], commits=[_commit("head", "yusra")],
        changed=[_changed("src/foo.py")],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "NOT_APPLICABLE"


def test_satisfied_when_owner_approved_head() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[_review("areej", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "SATISFIED"
    assert result.severity == "none"


def test_violated_when_owner_approved_stale_commit() -> None:
    """SBX-6 scenario: approve commit A, then push commit B."""
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[_review("areej", "APPROVED", "commit-A")],  # stale
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
        head_sha="head",
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert result.severity == "block"
    assert "head" in result.message or "payments/fees.py" in result.message


def test_insufficient_when_only_owner_is_pr_author() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @yusra\n"),
        reviews=[],  # no reviews but author can't self-approve
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert "yusra" in result.message


def test_satisfied_for_paths_with_no_owner_rule() -> None:
    """CODEOWNERS doesn't cover src/foo.py; C5 should still be SATISFIED."""
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("src/foo.py")],  # not under /payments/
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "SATISFIED"


def test_multiple_owners_any_approving_at_head_satisfies() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/fees.py @areej @yusra\n"),
        reviews=[_review("yusra", "APPROVED", "head")],  # one of two owners is enough
        commits=[_commit("head", "someone")],
        changed=[_changed("payments/fees.py")],
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "SATISFIED"
