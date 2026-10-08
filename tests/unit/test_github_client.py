"""Unit tests for the GitHub client. All HTTP mocked with respx — no network."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from codeatlas.collect.github import GitHubClient, GitHubClientConfig
from codeatlas.collect.github._errors import CollectionError


def _client() -> GitHubClient:
    return GitHubClient(GitHubClientConfig(token="fake-token"))


@respx.mock
def test_pull_request_returns_json() -> None:
    route = respx.get("https://api.github.com/repos/x/y/pulls/1").mock(
        return_value=Response(200, json={"number": 1, "title": "SBX-4: late fee"})
    )
    with _client() as client:
        pr = client.pull_request("x/y", 1)
    assert route.called
    assert pr["title"] == "SBX-4: late fee"


@respx.mock
def test_commits_paginates_until_no_next_link() -> None:
    first_page = [{"sha": f"c{i}", "commit": {"message": f"msg {i}"}} for i in range(100)]
    second_page = [{"sha": "c100", "commit": {"message": "msg 100"}}]
    respx.get("https://api.github.com/repos/x/y/pulls/1/commits?per_page=100").mock(
        return_value=Response(
            200,
            json=first_page,
            headers={
                "Link": '<https://api.github.com/repos/x/y/pulls/1/commits?per_page=100&page=2>; rel="next"'
            },
        )
    )
    respx.get(
        "https://api.github.com/repos/x/y/pulls/1/commits?per_page=100&page=2"
    ).mock(return_value=Response(200, json=second_page))
    with _client() as client:
        commits = client.commits("x/y", 1)
    assert len(commits) == 101
    assert commits[-1]["sha"] == "c100"


@respx.mock
def test_reviews_includes_commit_id_which_c5_depends_on() -> None:
    respx.get("https://api.github.com/repos/x/y/pulls/1/reviews?per_page=100").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 42,
                    "user": {"login": "areej8"},
                    "state": "APPROVED",
                    "commit_id": "abc123",
                    "submitted_at": "2026-10-05T12:00:00Z",
                },
            ],
        )
    )
    with _client() as client:
        reviews = client.reviews("x/y", 1)
    assert reviews[0]["commit_id"] == "abc123"


@respx.mock
def test_404_on_file_returns_none_not_raise() -> None:
    respx.get("https://api.github.com/repos/x/y/contents/CODEOWNERS?ref=abc").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )
    with _client() as client:
        assert client.file_at_commit("x/y", "CODEOWNERS", "abc") is None


@respx.mock
def test_non_404_error_raises_collection_error() -> None:
    respx.get("https://api.github.com/repos/x/y/pulls/1").mock(
        return_value=Response(401, json={"message": "Bad credentials"})
    )
    with _client() as client, pytest.raises(CollectionError) as exc:
        client.pull_request("x/y", 1)
    assert exc.value.status == 401
    assert exc.value.retryable is False


@respx.mock
def test_500_retries_then_raises() -> None:
    respx.get("https://api.github.com/repos/x/y/pulls/1").mock(
        return_value=Response(503, json={"message": "Service Unavailable"})
    )
    with _client() as client, pytest.raises(CollectionError) as exc:
        client.pull_request("x/y", 1)
    assert exc.value.status == 503
    assert exc.value.retryable is True
