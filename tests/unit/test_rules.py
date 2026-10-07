"""Tests for docs/specs/step-7-rules.md: every row of the fixture table (AC1-AC3, AC5, AC6).

Bundles are schema objects built in code (spec §13). Times are minutes after 2026-10-01T05:00Z.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from codeatlas.evaluate.rules import default_rules
from codeatlas.evaluate.rules.c1_changed_after_impl import ChangedAfterImplementation
from codeatlas.evaluate.rules.c3_not_approved import NotApproved
from codeatlas.evaluate.rules.c6_superseded_version import SupersededVersion
from codeatlas.evaluate.rules.c7_test_evidence import TestEvidenceStub
from codeatlas.evaluate.rules.c8_priority_authority import PriorityWithoutAuthority
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import (
    Approval,
    CasePolicy,
    ChangeEvent,
    CheckResult,
    Commit,
    EvidenceBundle,
    LifecycleFacts,
    PriorityChange,
    RequirementVersion,
    WorkItemRef,
)
from tests.samples import BUNDLE, POLICY

START = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
KEY = "SBX-2"


def t(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def edit(minutes: int, key: str = KEY, field: str = "description") -> ChangeEvent:
    return ChangeEvent(
        evidence_id=f"jira:{key}:history:{minutes}:{field}",
        source="jira",
        source_ref=f"https://example.atlassian.net/browse/{key}",
        retrieved_at=t(1000),
        extractor_version="test",
        key=key,
        history_id=str(minutes),
        field=field,
        from_value="old",
        to_value="new",
        actor_account_id="user-01",
        at=t(minutes),
    )


def commit(minutes: int) -> Commit:
    sha = f"{minutes:040d}"
    return Commit(
        evidence_id=f"git:commit:{sha}",
        source="git",
        source_ref=f"repo@{sha}",
        retrieved_at=t(1000),
        extractor_version="test",
        sha=sha,
        author_email="user-02@example.test",
        author_login="user-02",
        committed_at=t(minutes),
        message=f"{KEY}: work",
    )


def approval(minutes: int, *, valid: bool = True, revoked: int | None = None) -> Approval:
    return Approval(
        key=KEY,
        actor_account_id="user-01" if valid else "user-03",
        at=t(minutes),
        history_id=str(minutes),
        by_approver_group=valid,
        revoked_at=t(revoked) if revoked is not None else None,
        evidence_id=f"jira:{KEY}:history:{minutes}:status",
    )


def raise_priority(
    minutes: int, *, after: bool, authority: bool | None, actor: str | None = "user-03"
) -> PriorityChange:
    return PriorityChange(
        key=KEY,
        actor_account_id=actor,
        at=t(minutes),
        from_priority="Medium",
        to_priority="Highest",
        after_first_approval=after,
        actor_has_authority=authority,
        evidence_id=f"jira:{KEY}:history:{minutes}:priority",
    )


def facts(
    key: str = KEY,
    approvals: list[Approval] | None = None,
    edits: list[int] | None = None,
    priorities: list[PriorityChange] | None = None,
    labels: list[str] | None = None,
) -> LifecycleFacts:
    approvals = approvals or []
    valid = [a.at for a in approvals if a.by_approver_group and a.revoked_at is None]
    return LifecycleFacts(
        key=key,
        requester_account_id="user-01",
        approvals=approvals,
        latest_valid_approval_at=max(valid, default=None),
        last_requirement_edit_at=t(max(edits)) if edits else None,
        priority_changes=priorities or [],
        assignee_history=[(t(0), "user-02")],
        labels=labels or [],
    )


def version(key: str = KEY, *, resolved: bool = True) -> RequirementVersion:
    return RequirementVersion(
        key=key,
        version_no=1,
        valid_from=t(0),
        valid_to=None,
        summary="Return a book",
        description=None if not resolved else "A member returns a book.",
        acceptance_criteria=None,
        status="Approved",
        priority="Medium",
        assignee_account_id="user-02",
        content_resolved=resolved,
        unresolved_reasons=[] if resolved else ["description: chain broken"],
        unresolved_fields=[] if resolved else ["description"],
    )


def work_item(key: str = KEY) -> WorkItemRef:
    return WorkItemRef(
        evidence_id=f"github:pr:1:title:{key}",
        source="github",
        source_ref="https://github.com/codeatlas-fyp/codeatlas-sandbox/pull/1",
        retrieved_at=t(1000),
        extractor_version="test",
        key=key,
        found_in="pr_title",
        commit_sha=None,
    )


def bundle(
    *,
    keys: tuple[str, ...] = (KEY,),
    lifecycle: list[LifecycleFacts] | None = None,
    edits: list[ChangeEvent] | None = None,
    commits: list[int] | None = None,
    resolved: bool = True,
    notes: list[str] | None = None,
    policy: Any = POLICY,
) -> EvidenceBundle:
    return BUNDLE.model_copy(
        update={
            "work_items": [work_item(k) for k in keys],
            "lifecycle": lifecycle if lifecycle is not None else [facts(k) for k in keys],
            "versions": [version(k, resolved=resolved) for k in keys],
            "change_events": edits or [],
            "commits": [commit(m) for m in (commits or [])],
            "collection_notes": notes or [],
            "policy": policy,
        }
    )


def check(rule: Any, b: EvidenceBundle) -> CheckResult:
    result: CheckResult = rule.check(b, [], b.policy)
    assert result.case == rule.case
    return result


C1, C3, C6, C7, C8 = (
    ChangedAfterImplementation(),
    NotApproved(),
    SupersededVersion(),
    TestEvidenceStub(),
    PriorityWithoutAuthority(),
)


# --- C1 ------------------------------------------------------------------------------------------


def test_c1_pass_edits_before_first_commit() -> None:
    result = check(C1, bundle(edits=[edit(5)], commits=[10, 20]))

    assert result.outcome == "SATISFIED"


def test_c1_review_edit_after_first_commit() -> None:
    result = check(C1, bundle(edits=[edit(5), edit(15)], commits=[10, 20]))

    assert (result.outcome, result.severity, result.required) == ("VIOLATED", "review", True)
    assert result.evidence_ids == [commit(10).evidence_id, edit(15).evidence_id]


def test_c1_unknown_without_commits() -> None:
    assert check(C1, bundle(edits=[edit(5)])).outcome == "INSUFFICIENT_EVIDENCE"


def test_c1_edge_edit_at_the_first_commit_time_is_not_after() -> None:
    assert check(C1, bundle(edits=[edit(10)], commits=[10, 20])).outcome == "SATISFIED"


def test_c1_ignores_edits_to_non_requirement_fields() -> None:
    result = check(C1, bundle(edits=[edit(15, field="labels")], commits=[10]))

    assert result.outcome == "SATISFIED"


# --- C3 ------------------------------------------------------------------------------------------


def test_c3_pass_approved_after_last_edit() -> None:
    result = check(C3, bundle(lifecycle=[facts(approvals=[approval(5)], edits=[3])]))

    assert result.outcome == "SATISFIED"


def test_c3_fail_stale_approval_cites_both_ids() -> None:
    b = bundle(lifecycle=[facts(approvals=[approval(5)], edits=[9])], edits=[edit(9)])

    result = check(C3, b)

    assert (result.outcome, result.severity) == ("VIOLATED", "block")
    assert "stale approval" in result.message
    assert result.evidence_ids == [approval(5).evidence_id, edit(9).evidence_id]  # sorted
    assert evaluate_bundle(b, "a" * 64, rules=[C3]).value == "FAIL"


def test_c3_fail_never_approved() -> None:
    result = check(C3, bundle(lifecycle=[facts(approvals=[approval(5, valid=False)])]))

    assert (result.outcome, result.severity) == ("VIOLATED", "block")
    assert "not approved" in result.message


def test_c3_unknown_without_work_item() -> None:
    result = check(C3, bundle(keys=()))

    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert "no work item" in result.message


def test_c3_edge_reapproval_after_revocation() -> None:
    approvals = [approval(5, revoked=7), approval(9)]

    result = check(C3, bundle(lifecycle=[facts(approvals=approvals, edits=[8])]))

    assert result.outcome == "SATISFIED"


def test_c3_edge_broken_history_is_unknown() -> None:
    b = bundle(lifecycle=[facts(approvals=[approval(9)], edits=[5])], resolved=False)

    assert check(C3, b).outcome == "INSUFFICIENT_EVIDENCE"


def test_c3_edge_broken_history_still_reports_a_known_violation() -> None:
    b = bundle(lifecycle=[facts(approvals=[approval(5)], edits=[9])], resolved=False)

    assert check(C3, b).outcome == "VIOLATED"


def test_c3_edge_approvers_unknown_is_unknown() -> None:
    b = bundle(lifecycle=[facts()], notes=[f"approvers unknown: {KEY}"])

    assert check(C3, b).outcome == "INSUFFICIENT_EVIDENCE"


def test_c3_issue_not_found_is_unknown() -> None:
    assert check(C3, bundle(lifecycle=[])).outcome == "INSUFFICIENT_EVIDENCE"


# --- C6 ------------------------------------------------------------------------------------------


def test_c6_pass() -> None:
    assert check(C6, bundle(edits=[edit(5)], commits=[10, 20])).outcome == "SATISFIED"


def test_c6_review_edit_after_last_commit() -> None:
    result = check(C6, bundle(edits=[edit(25)], commits=[10, 20]))

    assert (result.outcome, result.severity) == ("VIOLATED", "review")
    assert result.evidence_ids == [commit(20).evidence_id, edit(25).evidence_id]


def test_c6_unknown_without_commits() -> None:
    assert check(C6, bundle(edits=[edit(25)])).outcome == "INSUFFICIENT_EVIDENCE"


def test_c6_edge_edit_between_commits_is_c1_not_c6() -> None:
    b = bundle(edits=[edit(15)], commits=[10, 20])

    assert check(C6, b).outcome == "SATISFIED"
    assert check(C1, b).outcome == "VIOLATED"


def test_c6_broken_history_is_unknown() -> None:
    assert check(C6, bundle(commits=[10], resolved=False)).outcome == "INSUFFICIENT_EVIDENCE"


# --- C8 ------------------------------------------------------------------------------------------


def c8_bundle(*changes: PriorityChange) -> EvidenceBundle:
    return bundle(lifecycle=[facts(approvals=[approval(5)], priorities=list(changes))])


def test_c8_pass_raised_by_authority() -> None:
    assert (
        check(C8, c8_bundle(raise_priority(8, after=True, authority=True))).outcome == "SATISFIED"
    )


def test_c8_review_raised_by_non_authority() -> None:
    change = raise_priority(8, after=True, authority=False)

    result = check(C8, c8_bundle(change))

    assert (result.outcome, result.severity) == ("VIOLATED", "review")
    assert result.evidence_ids == [change.evidence_id]


def test_c8_unknown_authority() -> None:
    result = check(C8, c8_bundle(raise_priority(8, after=True, authority=None)))

    assert result.outcome == "INSUFFICIENT_EVIDENCE"


def test_c8_edge_before_approval_and_automation_are_fine() -> None:
    changes = (
        raise_priority(3, after=False, authority=False),
        raise_priority(8, after=True, authority=None, actor=None),
    )

    assert check(C8, c8_bundle(*changes)).outcome == "SATISFIED"


def test_c8_unknown_without_work_item() -> None:
    assert check(C8, bundle(keys=())).outcome == "INSUFFICIENT_EVIDENCE"


# --- C7 stub -------------------------------------------------------------------------------------


def test_c7_not_applicable_without_label() -> None:
    result = check(C7, bundle())

    assert (result.outcome, result.required) == ("NOT_APPLICABLE", False)


def test_c7_label_requires_evidence_not_collected_yet() -> None:
    labelled = facts(labels=["requires-test-evidence"])

    result = check(C7, bundle(lifecycle=[labelled]))

    assert (result.outcome, result.required) == ("INSUFFICIENT_EVIDENCE", True)


# --- combining keys, policy severity -------------------------------------------------------------


def test_several_keys_one_stale() -> None:
    fine = facts(key="SBX-1", approvals=[approval(5)], edits=[3])
    stale = facts(approvals=[approval(5)], edits=[9])
    b = bundle(keys=("SBX-1", KEY), lifecycle=[fine, stale], edits=[edit(3, "SBX-1"), edit(9)])

    result = check(C3, b)

    assert result.outcome == "VIOLATED"
    assert edit(3, "SBX-1").evidence_id not in result.evidence_ids


def test_policy_overrides_severity() -> None:
    policy = POLICY.model_copy(update={"cases": {"C1": CasePolicy(severity="block")}})

    result = check(C1, bundle(edits=[edit(15)], commits=[10], policy=policy))

    assert result.severity == "block"


# --- AC3: no work item means UNKNOWN and nothing passes ------------------------------------------


def test_ac3_no_work_item_verdict_unknown() -> None:
    verdict = evaluate_bundle(bundle(keys=()), "a" * 64)

    assert verdict.value == "UNKNOWN"
    assert all(r.outcome != "SATISFIED" for r in verdict.results)
    phase1 = [r for r in verdict.results if r.case in ("C1", "C3", "C6", "C8")]
    assert len(phase1) == 4
    assert all("no work item" in r.message for r in phase1)


# --- AC6: default rules ------------------------------------------------------------------------


def test_ac6_default_rules() -> None:
    assert sorted(rule.case for rule in default_rules()) == ["C1", "C3", "C6", "C7", "C8"]


@pytest.mark.parametrize("rule", [C1, C3, C6, C7, C8], ids=lambda r: r.case)
def test_rules_declare_versions_and_evidence(rule: Any) -> None:
    assert rule.version
    assert set(rule.required_evidence) <= set(EvidenceBundle.model_fields)
