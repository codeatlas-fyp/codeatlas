"""Tests for `analyze.keys.find_keys`."""

from __future__ import annotations

from datetime import UTC, datetime

from codeatlas.analyze.keys import find_keys, keys_only

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def test_finds_key_in_branch_name() -> None:
    refs = find_keys(
        branch="feature/SBX-4-late-fee",
        pr_title="",
        pr_body="",
        commits=[],
        retrieved_at=NOW,
    )
    assert [r.key for r in refs] == ["SBX-4"]
    assert refs[0].found_in == "branch"
    assert refs[0].commit_sha is None


def test_finds_key_in_pr_title_and_body() -> None:
    refs = find_keys(
        branch="main",
        pr_title="SBX-4: late fee",
        pr_body="Also fixes PROJ-99",
        commits=[],
        retrieved_at=NOW,
    )
    found = {(r.key, r.found_in) for r in refs}
    assert ("SBX-4", "pr_title") in found
    assert ("PROJ-99", "pr_body") in found


def test_finds_keys_in_commit_messages_with_sha_recorded() -> None:
    commits = [
        {"sha": "aaa111", "commit": {"message": "feat(fee): calc (SBX-4)"}},
        {"sha": "bbb222", "commit": {"message": "chore: tidy"}},
    ]
    refs = find_keys(branch="", pr_title="", pr_body="", commits=commits, retrieved_at=NOW)
    assert len(refs) == 1
    assert refs[0].key == "SBX-4"
    assert refs[0].found_in == "commit"
    assert refs[0].commit_sha == "aaa111"


def test_same_key_in_multiple_places_appears_once_per_place() -> None:
    refs = find_keys(
        branch="feature/SBX-4",
        pr_title="SBX-4",
        pr_body="SBX-4 again",
        commits=[{"sha": "a1", "commit": {"message": "SBX-4 commit"}}],
        retrieved_at=NOW,
    )
    # One per source location.
    assert {r.found_in for r in refs} == {"branch", "pr_title", "pr_body", "commit"}
    # Dedup within a single source: the body mentions SBX-4 twice above (via
    # the whole text being one scan), but the dedup key is (key, found_in,
    # commit_sha) so we expect exactly 4 refs.
    assert len(refs) == 4


def test_keys_only_returns_unique_sorted_keys() -> None:
    refs = find_keys(
        branch="feature/B-2",
        pr_title="A-1 and B-2",
        pr_body="",
        commits=[],
        retrieved_at=NOW,
    )
    assert keys_only(refs) == ["A-1", "B-2"]


def test_no_keys_returns_empty_list() -> None:
    refs = find_keys(
        branch="hotfix/urgent",
        pr_title="urgent fix",
        pr_body="no ticket",
        commits=[{"sha": "a", "commit": {"message": "fix"}}],
        retrieved_at=NOW,
    )
    assert refs == []


def test_custom_pattern_works_for_other_prefix_styles() -> None:
    refs = find_keys(
        branch="feature/jira-1234-hello",
        pr_title="",
        pr_body="",
        commits=[],
        pattern=r"jira-\d+",
        retrieved_at=NOW,
    )
    assert len(refs) == 1 and refs[0].key == "jira-1234"
