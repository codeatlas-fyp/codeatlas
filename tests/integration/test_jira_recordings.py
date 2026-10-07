"""Integration tests on recorded SBX responses (step-5 spec AC11). Network is blocked."""

from pathlib import Path

import pytest

from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text
from codeatlas.collect.jira.recorder import FixtureJiraSource
from codeatlas.errors import CollectionError

FIXTURES = Path(__file__).parents[1] / "fixtures" / "jira"


@pytest.fixture
def source() -> FixtureJiraSource:
    return FixtureJiraSource(FIXTURES)


@pytest.mark.parametrize("key", [f"SBX-{n}" for n in range(1, 8)])
def test_ac11_every_demo_issue_is_recorded(source: FixtureJiraSource, key: str) -> None:
    issue = source.fetch_issue(key)

    assert issue["key"] == key
    assert adf_to_text(issue["fields"]["description"])
    assert isinstance(source.fetch_changelog(key), list)


def test_ac11_sbx6_has_three_description_edits_in_order(source: FixtureJiraSource) -> None:
    histories = source.fetch_changelog("SBX-6")

    items = [item for h in histories for item in h["items"]]
    assert [item["fieldId"] for item in items] == ["description"] * 3
    assert [h["created"] for h in histories] == sorted(h["created"] for h in histories)


def test_ac11_sbx6_last_edit_matches_current_description(source: FixtureJiraSource) -> None:
    issue = source.fetch_issue("SBX-6")
    last = source.fetch_changelog("SBX-6")[-1]["items"][0]

    assert wiki_to_text(last["toString"]) == adf_to_text(issue["fields"]["description"])


def test_ac11_sbx6_edits_chain_together(source: FixtureJiraSource) -> None:
    items = [h["items"][0] for h in source.fetch_changelog("SBX-6")]

    for before, after in zip(items, items[1:], strict=False):
        assert wiki_to_text(before["toString"]) == wiki_to_text(after["fromString"])


def test_ac11_unknown_issue_is_a_404(source: FixtureJiraSource) -> None:
    with pytest.raises(CollectionError) as raised:
        source.fetch_issue("SBX-999")

    assert raised.value.status == 404


def test_ac11_unknown_group_is_a_404_like_live_jira(source: FixtureJiraSource) -> None:
    with pytest.raises(CollectionError) as raised:
        source.group_members("sbx-requirement-approvers")

    assert raised.value.status == 404
