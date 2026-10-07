"""Lifecycle on recorded SBX issues (step-6 spec AC1, fixtures table). Network is blocked."""

from pathlib import Path

import pytest

from codeatlas.analyze.lifecycle import JiraHistory, reconstruct
from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text
from codeatlas.collect.jira.recorder import FixtureJiraSource
from tests.jira_builders import TEXT, context

FIXTURES = Path(__file__).parents[1] / "fixtures" / "jira"
SOURCE = FixtureJiraSource(FIXTURES)


def recorded(key: str) -> JiraHistory:
    return JiraHistory(issue=SOURCE.fetch_issue(key), changelog=SOURCE.fetch_changelog(key))


def test_ac1_sbx6_has_four_resolved_versions_matching_its_history() -> None:
    history = recorded("SBX-6")

    result = reconstruct(history, context(), TEXT)

    assert [v.version_no for v in result.versions] == [1, 2, 3, 4]
    assert all(v.content_resolved for v in result.versions)
    items = [h["items"][0] for h in sorted(history.changelog, key=lambda h: int(h["id"]))]
    expected = [wiki_to_text(items[0]["fromString"])] + [wiki_to_text(i["toString"]) for i in items]
    assert [v.description for v in result.versions] == expected
    assert result.versions[-1].description == adf_to_text(history.issue["fields"]["description"])
    assert [e.field for e in result.events] == ["description"] * 3
    assert result.facts.requester_account_id == "user-01"


@pytest.mark.parametrize("key", ["SBX-1", "SBX-2", "SBX-3", "SBX-4", "SBX-5", "SBX-7"])
def test_issues_without_history_have_one_version(key: str) -> None:
    result = reconstruct(recorded(key), context(), TEXT)

    (version,) = result.versions
    assert version.content_resolved
    assert version.status == "To Do"
    assert result.events == []
    assert result.facts.labels and result.facts.labels[0].startswith("scenario-")
