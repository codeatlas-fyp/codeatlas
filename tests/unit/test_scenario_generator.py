"""Tests for the scenario generator (tools/scenario_generator): plans, oracle, guards, runner.

Jira is mocked with respx or a fake writer; nothing here writes to a real Jira.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from tools.scenario_generator.jira_writer import JiraWriter, RefusedError
from tools.scenario_generator.oracle import Done, expected
from tools.scenario_generator.plan import Action, IssuePlan, make_plans
from tools.scenario_generator.run import jira_times, run

BASE = "https://example.atlassian.net"
API = BASE + "/rest/api/3"
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def t(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- plans ----------------------------------------------------------------------------------------


def test_plans_are_reproducible_from_the_seed() -> None:
    assert make_plans(20, seed=7) == make_plans(20, seed=7)
    assert make_plans(20, seed=7) != make_plans(20, seed=8)


def test_every_plan_has_both_commit_markers_in_order_and_valid_status_moves() -> None:
    for plan in make_plans(200, seed=1):
        kinds = [a.kind for a in plan.actions]
        assert kinds.count("first_commit") == kinds.count("last_commit") == 1
        assert kinds.index("first_commit") < kinds.index("last_commit")
        approved = False
        for kind in kinds:
            if kind == "approve":
                assert not approved
                approved = True
            elif kind == "unapprove":
                assert approved
                approved = False


# --- oracle --------------------------------------------------------------------------------------


def log(*entries: tuple[str, int]) -> list[Done]:
    return [Done(kind, t(s), f"text {s}" if kind == "edit" else None) for kind, s in entries]


def test_oracle_stale_approval_and_edit_after_commits() -> None:
    truth = expected(
        "v1",
        log(("first_commit", 1), ("approve", 3), ("edit", 6), ("last_commit", 8), ("edit", 9)),
    )

    assert truth.versions == ["v1", "text 6", "text 9"]
    assert (truth.c3, truth.c3_reason) == ("VIOLATED", "stale approval")
    assert (truth.c1, truth.c6) == ("VIOLATED", "VIOLATED")


def test_oracle_revoked_then_never_reapproved_is_not_approved() -> None:
    truth = expected(
        "v1", log(("approve", 3), ("unapprove", 6), ("first_commit", 7), ("last_commit", 8))
    )

    assert (truth.c3, truth.c3_reason) == ("VIOLATED", "not approved")
    assert (truth.c1, truth.c6) == ("SATISFIED", "SATISFIED")


def test_oracle_reapproval_after_edit_is_approved() -> None:
    truth = expected(
        "v1",
        log(
            ("approve", 3),
            ("unapprove", 4),
            ("edit", 6),
            ("approve", 9),
            ("first_commit", 10),
            ("last_commit", 11),
        ),
    )

    assert (truth.c3, truth.c3_reason) == ("SATISFIED", "approved")


def test_oracle_priority_after_first_approval_depends_on_authority() -> None:
    after = expected(
        "v", log(("approve", 3), ("priority", 6), ("first_commit", 7), ("last_commit", 8))
    )
    before = expected(
        "v", log(("priority", 2), ("approve", 3), ("first_commit", 7), ("last_commit", 8))
    )

    assert (after.c8_with_authority, after.c8_without_authority) == ("SATISFIED", "VIOLATED")
    assert before.c8_without_authority == "SATISFIED"


# --- matching actions to Jira's changelog --------------------------------------------------------


def history(history_id: int, seconds: int, field: str) -> dict[str, Any]:
    stamp = (t(seconds) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000+0500")
    return {"id": str(history_id), "created": stamp, "items": [{"field": field, "fieldId": field}]}


def test_jira_times_take_jira_timestamps_and_place_markers_between_actions() -> None:
    plan = IssuePlan(
        "s",
        "v1",
        [
            Action("first_commit"),
            Action("edit", text="v2"),
            Action("approve"),
            Action("last_commit"),
            Action("priority", priority="High"),
        ],
    )
    changelog = [history(1, 4, "description"), history(2, 8, "status"), history(3, 14, "priority")]

    done = jira_times(plan, changelog, created=t(0))

    assert [(d.kind, d.at) for d in done] == [
        ("first_commit", t(2)),
        ("edit", t(4)),
        ("approve", t(8)),
        ("last_commit", t(11)),
        ("priority", t(14)),
    ]


def test_trailing_marker_is_two_seconds_after_the_last_action() -> None:
    plan = IssuePlan(
        "s", "v1", [Action("edit", text="v2"), Action("first_commit"), Action("last_commit")]
    )

    done = jira_times(plan, [history(1, 4, "description")], created=t(0))

    assert [d.at for d in done] == [t(4), t(6), t(6)]


# --- writer guards -------------------------------------------------------------------------------


def writer() -> JiraWriter:
    return JiraWriter(BASE, "user-01@example.test", "token", project="SBX", sleep=lambda s: None)


def test_writer_refuses_any_other_project() -> None:
    with pytest.raises(RefusedError, match="only writes to SBX"):
        JiraWriter(BASE, "e", "t", project="CA")


@pytest.mark.parametrize("key", ["SBX-1", "SBX-7", "CA-12"])
def test_writer_refuses_demo_and_foreign_issues(key: str) -> None:
    with pytest.raises(RefusedError, match="not a generated"):
        writer().edit_description(key, "x")


@respx.mock
def test_writer_refuses_an_issue_without_the_generated_label() -> None:
    respx.get(f"{API}/issue/SBX-40").respond(200, json={"fields": {"labels": ["scenario-c3-pass"]}})
    put = respx.put(f"{API}/issue/SBX-40")

    with pytest.raises(RefusedError, match="not labelled 'generated'"):
        writer().edit_description("SBX-40", "x")

    assert not put.called


@respx.mock
def test_writer_waits_on_429_then_edits() -> None:
    waits: list[float] = []
    w = JiraWriter(BASE, "e", "t", project="SBX", sleep=waits.append, pause=0.0)
    respx.get(f"{API}/issue/SBX-40").respond(200, json={"fields": {"labels": ["generated"]}})
    respx.put(f"{API}/issue/SBX-40").side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(204),
    ]

    w.edit_description("SBX-40", "new text")

    assert 3.0 in waits


@respx.mock
def test_missing_approved_transition_is_refused_not_guessed() -> None:
    respx.get(f"{API}/issue/SBX-40").respond(200, json={"fields": {"labels": ["generated"]}})
    respx.get(f"{API}/issue/SBX-40/transitions").respond(
        200,
        json={
            "transitions": [
                {"id": "11", "to": {"name": "To Do"}},
                {"id": "31", "to": {"name": "Done"}},
            ]
        },
    )

    with pytest.raises(RefusedError, match="no transition into 'Approved'"):
        writer().move_to("SBX-40", "Approved")


# --- runner with a fake writer -------------------------------------------------------------------


class FakeWriter:
    """Records actions and produces a changelog with one history per action, 3 s apart."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.histories: list[dict[str, Any]] = []

    def _log(self, field: str) -> None:
        n = len(self.histories) + 1
        self.histories.append(history(n, 3 * n, field))

    def create_issue(self, summary: str, description: str, run_label: str) -> str:
        self.calls.append(("create", run_label))
        return "SBX-100"

    def edit_description(self, key: str, text: str) -> None:
        self.calls.append(("edit", text))
        self._log("description")

    def move_to(self, key: str, status: str) -> None:
        self.calls.append(("move", status))
        self._log("status")

    def set_priority(self, key: str, priority: str) -> None:
        self.calls.append(("priority", priority))
        self._log("priority")

    def created(self, key: str) -> str:
        return (T0 + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.000+0500")

    def changelog(self, key: str) -> list[dict[str, Any]]:
        return self.histories


def test_run_records_ground_truth_for_every_action() -> None:
    plan = make_plans(1, seed=3)[0]
    fake = FakeWriter()

    (record,) = run(fake, [plan], "gen-test", sleep=lambda s: None)  # type: ignore[arg-type]

    performed = [a for a in plan.actions if a.kind not in ("first_commit", "last_commit")]
    assert len(fake.calls) == 1 + len(performed)
    assert record["key"] == "SBX-100"
    assert [a["kind"] for a in record["actions"]] == [a.kind for a in plan.actions]
    assert record["expected"]["versions"][0] == plan.description
    markers = [a for a in record["actions"] if a["kind"].endswith("_commit")]
    assert all(a["at"] >= "2026-10-08T09:00:00Z" for a in markers)  # Jira's clock, not ours
