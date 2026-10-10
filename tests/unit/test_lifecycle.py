"""Tests for docs/specs/step-6-lifecycle.md (AC3, AC5-AC11) on histories built in code."""

import pytest

from codeatlas.analyze.lifecycle import comparison_form, parse_time, reconstruct
from tests.jira_builders import (
    APPROVER,
    OUTSIDER,
    TEXT,
    context,
    history,
    issue,
    item,
    source,
    status,
    utc,
)

V1 = "A member renews a loan."
V2 = "A member renews a loan.\nA loan can be renewed twice."
V3 = "A member renews a loan.\nA loan can be renewed three times."


def three_versions() -> tuple[dict[str, object], ...]:
    return (
        history(1, 10, item("description", V1, V2)),
        history(2, 20, item("description", V2, V3)),
    )


# --- versions ---------------------------------------------------------------------------------


def test_versions_follow_edits_and_end_at_current_value() -> None:
    result = reconstruct(source(issue(description=V3), *three_versions()), context(), TEXT)

    texts = [v.description for v in result.versions]
    assert texts == [V1, V2, V3]
    assert [v.version_no for v in result.versions] == [1, 2, 3]
    assert [v.valid_from for v in result.versions] == [utc(0), utc(10), utc(20)]
    assert [v.valid_to for v in result.versions] == [utc(10), utc(20), None]
    assert all(v.content_resolved for v in result.versions)
    assert result.facts.last_requirement_edit_at == utc(20)


def test_ac3_broken_chain_unresolves_only_older_versions_and_keeps_events() -> None:
    edits = (
        history(1, 10, item("description", V1, V2)),
        history(2, 20, item("description", "something never shown", V3)),
    )

    result = reconstruct(source(issue(description=V3), *edits), context(), TEXT)

    v1, v2, v3 = result.versions
    assert (v1.description, v2.description) == (None, None)
    assert v1.unresolved_fields == v2.unresolved_fields == ["description"]
    assert not v1.content_resolved
    assert "history 1" in v1.unresolved_reasons[0]
    assert v3.description == V3 and v3.content_resolved
    assert [e.history_id for e in result.events] == ["1", "2"]
    assert v1.summary == "Renew a loan"  # other fields keep their text


def test_ac3_newest_change_not_matching_current_value_unresolves_all() -> None:
    edits = (history(1, 10, item("description", V1, V2)),)

    result = reconstruct(source(issue(description=V3), *edits), context(), TEXT)

    assert [v.description for v in result.versions] == [None, None]
    assert "current value" in result.versions[0].unresolved_reasons[0]
    assert len(result.events) == 1


def test_ac5_two_fields_in_one_history_make_one_version() -> None:
    edit = history(
        1,
        10,
        item("summary", "Renew", "Renew a loan"),
        item("description", V1, V2),
    )

    result = reconstruct(source(issue(description=V2), edit), context(), TEXT)

    v1, v2 = result.versions
    assert (v1.summary, v1.description) == ("Renew", V1)
    assert (v2.summary, v2.description) == ("Renew a loan", V2)
    assert len(result.events) == 2


def test_ac6_no_requirement_changes_gives_one_version() -> None:
    result = reconstruct(source(issue(), status(1, 5, "To Do", "In Progress")), context(), TEXT)

    (version,) = result.versions
    assert version.description == V1
    assert version.status == "To Do"  # status at valid_from, before the move
    assert result.facts.last_requirement_edit_at is None


def test_missing_requirement_field_is_none_everywhere() -> None:
    result = reconstruct(source(issue(description=None)), context(), TEXT)

    assert result.versions[0].description is None
    assert result.versions[0].acceptance_criteria is None
    assert result.versions[0].content_resolved


def test_comparison_form_ignores_markers_and_spacing() -> None:
    assert comparison_form("1.  Reject   tokens\n\n- Allow skew ") == "Reject tokens\nAllow skew"
    assert comparison_form(None) == ""


# --- approvals (AC7, AC8) --------------------------------------------------------------------


def test_ac7_approval_by_approver_is_valid() -> None:
    result = reconstruct(source(issue(), status(1, 5, "To Do", "Approved")), context(), TEXT)

    (approval,) = result.facts.approvals
    assert approval.by_approver_group
    assert approval.revoked_at is None
    assert approval.evidence_id == "jira:SBX-9:history:1:status"
    assert result.facts.latest_valid_approval_at == utc(5)


def test_ac7_approval_by_outsider_does_not_count() -> None:
    move = status(1, 5, "To Do", "Approved", author=OUTSIDER)

    result = reconstruct(source(issue(), move), context(), TEXT)

    assert not result.facts.approvals[0].by_approver_group
    assert result.facts.latest_valid_approval_at is None


def test_ac7_move_back_to_new_category_revokes_and_reapproval_counts() -> None:
    moves = (
        status(1, 5, "To Do", "Approved"),
        status(2, 8, "Approved", "To Do"),
        status(3, 12, "To Do", "Approved"),
    )

    result = reconstruct(source(issue(), *moves), context(), TEXT)

    first, second = result.facts.approvals
    assert first.revoked_at == utc(8)
    assert second.revoked_at is None
    assert result.facts.latest_valid_approval_at == utc(12)


@pytest.mark.parametrize("target", ["In Progress", "Done"])
def test_ac7_moving_forward_does_not_revoke(target: str) -> None:
    moves = (status(1, 5, "To Do", "Approved"), status(2, 8, "Approved", target))

    result = reconstruct(source(issue(), *moves), context(), TEXT)

    assert result.facts.approvals[0].revoked_at is None
    assert result.facts.latest_valid_approval_at == utc(5)


def test_ac7_approver_listed_by_account_id_only() -> None:
    move = status(1, 5, "To Do", "Approved", author="user-07")

    result = reconstruct(source(issue(), move), context(approvers=frozenset({"user-07"})), TEXT)

    assert result.facts.approvals[0].by_approver_group


def test_ac8_edit_after_approval_is_visible_with_both_evidence_ids() -> None:
    changes = (
        status(1, 5, "To Do", "Approved"),
        history(2, 9, item("description", V1, V2)),
    )

    result = reconstruct(source(issue(description=V2), *changes), context(), TEXT)

    facts = result.facts
    assert facts.last_requirement_edit_at is not None
    assert facts.latest_valid_approval_at is not None
    assert facts.last_requirement_edit_at > facts.latest_valid_approval_at
    ids = {e.evidence_id for e in result.events}
    assert {"jira:SBX-9:history:1:status", "jira:SBX-9:history:2:description"} <= ids


# --- priority (AC9) -------------------------------------------------------------------------


def priority(history_id: int, minutes: int, author: str | None) -> dict[str, object]:
    return history(history_id, minutes, item("priority", "Medium", "Highest"), author=author)


def test_ac9_priority_change_flags() -> None:
    changes = (
        priority(1, 2, OUTSIDER),
        status(2, 5, "To Do", "Approved"),
        priority(3, 8, OUTSIDER),
        priority(4, 9, APPROVER),
        priority(5, 11, None),
    )

    result = reconstruct(source(issue(priority="Highest"), *changes), context(), TEXT)

    flags = [(p.after_first_approval, p.actor_has_authority) for p in result.facts.priority_changes]
    assert flags == [(False, False), (True, False), (True, True), (True, None)]
    assert result.facts.priority_changes[1].evidence_id == "jira:SBX-9:history:3:priority"
    assert result.facts.priority_changes[1].to_priority == "Highest"


def test_ac9_unknown_authority_gives_none() -> None:
    changes = (status(1, 5, "To Do", "Approved"), priority(2, 8, OUTSIDER))

    result = reconstruct(source(issue(), *changes), context(authorities=None), TEXT)

    assert result.facts.priority_changes[0].actor_has_authority is None


def test_ac9_priority_before_any_valid_approval() -> None:
    result = reconstruct(source(issue(), priority(1, 8, OUTSIDER)), context(), TEXT)

    assert not result.facts.priority_changes[0].after_first_approval


# --- AC10: values at valid_from, assignee history, labels -----------------------------------


def test_ac10_fields_in_force_at_each_version() -> None:
    changes = (
        status(1, 5, "To Do", "Approved"),
        history(2, 6, item("assignee", "User 02", "User 04", from_id="user-02", to_id="user-04")),
        history(3, 7, item("priority", "Medium", "High")),
        history(4, 10, item("description", V1, V2)),
    )

    result = reconstruct(
        source(
            issue(
                description=V2,
                status="Approved",
                priority="High",
                assignee="user-04",
                labels=["scenario-x"],
            ),
            *changes,
        ),
        context(),
        TEXT,
    )

    v1, v2 = result.versions
    assert (v1.status, v1.priority, v1.assignee_account_id) == ("To Do", "Medium", "user-02")
    assert (v2.status, v2.priority, v2.assignee_account_id) == ("Approved", "High", "user-04")
    assert result.facts.assignee_history == [(utc(0), "user-02"), (utc(6), "user-04")]
    assert result.facts.labels == ["scenario-x"]
    assert result.facts.requester_account_id == "user-01"
    event = next(e for e in result.events if e.field == "assignee")
    assert (event.from_value, event.to_value) == ("user-02", "user-04")


def test_ac10_times_are_utc_seconds() -> None:
    assert parse_time("2026-10-07T16:27:48.950+0500") == parse_time("2026-10-07T11:27:48Z")
    assert parse_time("2026-10-07T16:27:48.950+0500").microsecond == 0


# --- AC11: order independence ----------------------------------------------------------------


def test_ac11_changelog_order_does_not_matter() -> None:
    forward = reconstruct(source(issue(description=V3), *three_versions()), context(), TEXT)
    backward = reconstruct(
        source(issue(description=V3), *reversed(three_versions())), context(), TEXT
    )

    assert backward == forward


def test_events_carry_jira_evidence_fields() -> None:
    result = reconstruct(source(issue(description=V3), *three_versions()), context(), TEXT)

    event = result.events[0]
    assert event.source == "jira"
    assert event.source_ref == "https://example.atlassian.net/browse/SBX-9"
    assert event.actor_account_id == APPROVER
    assert (event.from_value, event.to_value) == (V1, V2)
    assert event.extractor_version == "test"


def test_duplicate_field_in_one_history_gets_distinct_evidence_ids() -> None:
    edit = history(1, 5, item("labels", "", "a"), item("labels", "a", "a b"))

    result = reconstruct(source(issue(), edit), context(), TEXT)

    assert [e.evidence_id for e in result.events] == [
        "jira:SBX-9:history:1:labels",
        "jira:SBX-9:history:1:labels:2",
    ]


def test_ac10_change_in_the_same_history_is_in_force_for_that_version() -> None:
    # One history moves the status and edits the description: version 2 starts in the new status.
    edit = history(1, 10, item("status", "To Do", "In Progress"), item("description", V1, V2))

    result = reconstruct(source(issue(description=V2, status="In Progress"), edit), context(), TEXT)

    v1, v2 = result.versions
    assert (v1.status, v2.status) == ("To Do", "In Progress")


# --- boundaries found by the mutation run (docs/report/data/mutation-score.json) --------------


def test_custom_field_events_use_the_field_id() -> None:
    # Jira items carry a display name (`field`) and an id (`fieldId`); the id must win.
    raw = item("Acceptance Criteria", "old", "new")
    raw["fieldId"] = "customfield_10042"

    result = reconstruct(source(issue(), history(1, 5, raw)), context(), TEXT)

    assert result.events[0].field == "customfield_10042"
    assert result.events[0].evidence_id == "jira:SBX-9:history:1:customfield_10042"


def test_priority_change_at_the_approval_instant_is_not_after_it() -> None:
    same_time = history(
        1, 5, item("status", "To Do", "Approved"), item("priority", "Medium", "High")
    )

    result = reconstruct(source(issue(priority="High"), same_time), context(), TEXT)

    assert not result.facts.priority_changes[0].after_first_approval
