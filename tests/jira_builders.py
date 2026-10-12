"""Build Jira-shaped issue and changelog dicts in code for lifecycle tests.

The shapes copy the recorded SBX responses (tests/fixtures/jira/); values are invented. These
are inputs to a pure function inside tests, not committed fixture files.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from codeatlas.analyze.lifecycle import JiraHistory, LifecycleContext, TextForms
from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text
from codeatlas.schema import Policy
from tests.samples import POLICY

START = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
TEXT = TextForms(current=adf_to_text, history=wiki_to_text)
APPROVER = "user-01"
OUTSIDER = "user-03"


def at(minutes: int) -> str:
    """A Jira timestamp `minutes` after START, written in +05:00 local time as Jira does."""
    local = START + timedelta(minutes=minutes, hours=5)
    return local.strftime("%Y-%m-%dT%H:%M:%S.000+0500")


def utc(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def adf(text: str) -> dict[str, Any]:
    paragraphs = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.split("\n")
    ]
    return {"type": "doc", "version": 1, "content": paragraphs}


def issue(
    *,
    key: str = "SBX-9",
    summary: str = "Renew a loan",
    description: str | None = "A member renews a loan.",
    status: str = "To Do",
    priority: str = "Medium",
    assignee: str | None = "user-02",
    reporter: str = "user-01",
    labels: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "self": "https://example.atlassian.net/rest/api/3/issue/10009",
        "key": key,
        "fields": {
            "summary": summary,
            "description": adf(description) if description is not None else None,
            "status": {"name": status},
            "priority": {"name": priority},
            "assignee": {"accountId": assignee} if assignee else None,
            "reporter": {"accountId": reporter},
            "created": at(0),
            "labels": labels or [],
        },
    }


def item(
    field: str,
    from_string: str | None,
    to_string: str | None,
    *,
    from_id: str | None = None,
    to_id: str | None = None,
) -> dict[str, Any]:
    return {
        "field": field,
        "fieldId": field,
        "fieldtype": "jira",
        "from": from_id,
        "fromString": from_string,
        "to": to_id,
        "toString": to_string,
    }


def history(
    history_id: int, minutes: int, *items: dict[str, Any], author: str | None = APPROVER
) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": str(history_id), "created": at(minutes), "items": list(items)}
    if author is not None:
        entry["author"] = {"accountId": author}
    return entry


def status(history_id: int, minutes: int, old: str, new: str, *, author: str = APPROVER) -> Any:
    return history(history_id, minutes, item("status", old, new), author=author)


def context(
    *,
    policy: Policy = POLICY,
    approvers: frozenset[str] = frozenset({APPROVER}),
    authorities: frozenset[str] | None = frozenset({APPROVER}),
) -> LifecycleContext:
    return LifecycleContext(
        policy=policy,
        approvers=approvers,
        authorities=authorities,
        new_statuses=frozenset({"To Do"}),
        retrieved_at=utc(10_000),
        extractor_version="test",
    )


def source(issue_: dict[str, Any], *histories: dict[str, Any]) -> JiraHistory:
    return JiraHistory(issue=issue_, changelog=list(histories))
