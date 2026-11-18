"""Tests for C5 (code owner did not approve the head commit)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codeatlas.analyze.governance import parse_codeowners
from codeatlas.evaluate.rules.c5_owner_did_not_approve import C5OwnerDidNotApprove
from codeatlas.evaluate.verdict import decide
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
        source="analysis",
        source_ref="base@base",
        retrieved_at=NOW,
        extractor_version="governance@0.1.0",
        base_sha="base",
        codeowners_path="CODEOWNERS" if codeowners_text else None,
        owner_rules=rules,
        adrs=[],
        policy_blob_sha=None,
    )


def _commit(sha: str, login: str | None) -> Commit:
    return Commit(
        evidence_id=f"commit:{sha}",
        source="github",
        source_ref=f"commit:{sha}",
        retrieved_at=NOW,
        extractor_version="github@0.1.0",
        sha=sha,
        author_email=f"{login or 'anon'}@example.test",
        author_login=login,
        committed_at=NOW,
        message="work",
    )


def _review(reviewer: str, state: str, commit_id: str) -> Review:
    return Review(
        evidence_id=f"review:{reviewer}:{commit_id}",
        source="github",
        source_ref=f"review:{reviewer}:{commit_id}",
        retrieved_at=NOW,
        extractor_version="github@0.1.0",
        reviewer_login=reviewer,
        state=state,  # type: ignore[arg-type]
        commit_id=commit_id,
        submitted_at=NOW,
    )


def _changed(file: str) -> ChangedEntity:
    return ChangedEntity(
        evidence_id=f"changed:{file}",
        source="analysis",
        source_ref=f"diff:{file}",
        retrieved_at=NOW,
        extractor_version="code@0.1.0",
        entity_id=None,
        change="modified",
        file=file,
        hunk_lines=(1, 5),
        commit_shas=["head"],
    )


def _bundle(
    *,
    governance: GovernanceSnapshot,
    reviews: list[Review],
    commits: list[Commit],
    changed: list[ChangedEntity],
    head_sha: str = "head",
    pr_author_login: str | None = "someone",
) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="0",
        change=ChangeRef(
            repo="x/y", pr_number=1, base_sha="base", head_sha=head_sha, merge_base_sha="base"
        ),
        pr_author_login=pr_author_login,
        codeatlas_version="0.1.0",
        collected_at=NOW,
        run_id="r",
        extractor_versions={},
        model_ids=[],
        model_file_hashes={},
        policy=_policy(),
        identity_map=IdentityMap(people=[]),
        work_items=[],
        change_events=[],
        versions=[],
        lifecycle=[],
        commits=commits,
        reviews=reviews,
        people=[],
        governance=governance,
        governance_changes=[],
        entities=[],
        changed=changed,
        edges=[],
        criteria=[],
        candidates=[],
        collection_notes=[],
    )


def test_not_applicable_when_no_codeowners_at_base() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance(None),
        reviews=[],
        commits=[_commit("head", "yusra")],
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
        pr_author_login="yusra",
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.required is True
    assert "yusra" in result.message


def test_unresolved_owner_on_mixed_paths_gives_unknown() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n/platform/ @org/platform-team\n"),
        reviews=[_review("areej", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py"), _changed("platform/service.py")],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.required is True
    assert decide([result]) == "UNKNOWN"


def test_unresolved_owner_does_not_hide_a_violated_path() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n/platform/ @org/platform-team\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py"), _changed("platform/service.py")],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "VIOLATED"
    assert result.required is False
    assert decide([result]) == "FAIL"


def test_email_owner_is_unresolved_and_gives_unknown() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ owner@example.com\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.required is True
    assert decide([result]) == "UNKNOWN"


def test_email_owner_does_not_hide_a_violated_path() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n/platform/ owner@example.com\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py"), _changed("platform/service.py")],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "VIOLATED"
    assert decide([result]) == "FAIL"


def test_not_applicable_for_paths_with_no_owner_rule() -> None:
    """CODEOWNERS doesn't cover src/foo.py; no ownership obligation applies."""
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("src/foo.py")],  # not under /payments/
    )
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "NOT_APPLICABLE"


def test_ownerless_last_match_makes_path_not_applicable_in_c5() -> None:
    rule = C5OwnerDidNotApprove()
    bundle = _bundle(
        governance=_governance("/apps/ @areej\n/apps/github\n"),
        reviews=[],
        commits=[_commit("head", "yusra")],
        changed=[_changed("apps/github/x.py")],
    )

    result = rule.check(bundle, [], bundle.policy)

    assert result.outcome == "NOT_APPLICABLE"


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


def test_commit_author_approval_cannot_prove_independence_with_multiple_owners() -> None:
    bundle = _bundle(
        governance=_governance("/payments/ @areej @yusra\n"),
        reviews=[_review("yusra", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
        pr_author_login=None,
    )
    result = C5OwnerDidNotApprove().check(bundle, [], bundle.policy)
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert decide([result]) == "UNKNOWN"


def test_author_approval_does_not_count_when_other_owner_exists() -> None:
    bundle = _bundle(
        governance=_governance("/payments/ @areej @yusra\n"),
        reviews=[_review("yusra", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
        pr_author_login="yusra",
    )
    assert C5OwnerDidNotApprove().check(bundle, [], bundle.policy).outcome == "VIOLATED"


def test_commit_author_who_is_not_pr_author_can_approve() -> None:
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[_review("areej", "APPROVED", "head")],
        commits=[_commit("head", "areej")],
        changed=[_changed("payments/fees.py")],
        pr_author_login="yusra",
    )
    assert C5OwnerDidNotApprove().check(bundle, [], bundle.policy).outcome == "SATISFIED"


def test_later_changes_requested_supersedes_approval() -> None:
    change_request = _review("areej", "CHANGES_REQUESTED", "head").model_copy(
        update={"submitted_at": NOW + timedelta(seconds=1), "evidence_id": "review:later"}
    )
    bundle = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[_review("areej", "APPROVED", "head"), change_request],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
        pr_author_login="yusra",
    )
    assert C5OwnerDidNotApprove().check(bundle, [], bundle.policy).outcome == "VIOLATED"


def test_independent_owner_approval_satisfies_with_unresolved_other_owner() -> None:
    bundle = _bundle(
        governance=_governance("/payments/ @areej @org/team\n"),
        reviews=[_review("areej", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
    )
    assert C5OwnerDidNotApprove().check(bundle, [], bundle.policy).outcome == "SATISFIED"


def test_legacy_bundle_replays_original_c5_without_author_evidence(tmp_path) -> None:
    from codeatlas.bundle.replay import replay
    from codeatlas.bundle.store import BundleStore
    from codeatlas.pipeline import evaluate_bundle

    current = _bundle(
        governance=_governance("/payments/ @areej\n"),
        reviews=[_review("areej", "APPROVED", "head")],
        commits=[_commit("head", "yusra")],
        changed=[_changed("payments/fees.py")],
        pr_author_login=None,
    )
    assert C5OwnerDidNotApprove().check(current, [], current.policy).outcome == (
        "INSUFFICIENT_EVIDENCE"
    )
    legacy = EvidenceBundle.model_validate(current.model_dump(exclude={"pr_author_login"}))
    store = BundleStore(tmp_path)
    bundle_hash = store.save(legacy)
    verdict = evaluate_bundle(legacy, bundle_hash)
    assert "C5@1" in verdict.ruleset_version
    assert next(result for result in verdict.results if result.case == "C5").outcome == "SATISFIED"
    store.save_verdict(verdict)
    assert replay(store.folder(bundle_hash), evaluate_bundle).match


def test_new_bundle_uses_versioned_c5() -> None:
    from codeatlas.pipeline import evaluate_bundle

    bundle = _bundle(governance=_governance(), reviews=[], commits=[], changed=[])
    assert "C5@2" in evaluate_bundle(bundle, "a" * 64).ruleset_version
