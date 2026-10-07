"""Tests for docs/specs/step-5-jira-collector.md AC1-AC5 (client), with HTTP mocked by respx."""

import httpx
import pytest
import respx

from codeatlas.collect.jira.client import JiraClient, ReadOnlyTransport
from codeatlas.errors import CollectionError, SourceUnavailable

BASE = "https://example.atlassian.net"
API = BASE + "/rest/api/3"


@pytest.fixture
def waits() -> list[float]:
    return []


@pytest.fixture
def client(waits: list[float]) -> JiraClient:
    return JiraClient(BASE, "user-01@example.test", "token", sleep=waits.append, jitter=lambda: 1.0)


def page(values: list[object], start: int, is_last: bool) -> dict[str, object]:
    return {"startAt": start, "maxResults": 2, "total": 5, "isLast": is_last, "values": values}


# --- AC1: base URL, auth, read-only ----------------------------------------------------------


@respx.mock
def test_ac1_issue_uses_api_v3_basic_auth_and_all_fields(client: JiraClient) -> None:
    route = respx.get(f"{API}/issue/SBX-6").respond(200, json={"key": "SBX-6"})

    assert client.fetch_issue("SBX-6") == {"key": "SBX-6"}
    request = route.calls.last.request
    assert request.url.params["fields"] == "*all"
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.method == "GET"


def test_ac1_transport_refuses_non_get() -> None:
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request must not be sent")

    transport = ReadOnlyTransport(httpx.MockTransport(never))
    with httpx.Client(transport=transport) as http:
        with pytest.raises(CollectionError, match="read-only"):
            http.post("https://example.atlassian.net/rest/api/3/issue", json={})


def test_ac1_invalid_issue_key_is_rejected(client: JiraClient) -> None:
    with pytest.raises(ValueError, match="not a Jira issue key"):
        client.fetch_issue("../myself")


# --- AC2: pagination ---------------------------------------------------------------------------


@respx.mock
def test_ac2_changelog_follows_every_page_in_order(client: JiraClient) -> None:
    route = respx.get(f"{API}/issue/SBX-6/changelog")
    route.side_effect = [
        httpx.Response(200, json=page([{"id": "1"}, {"id": "2"}], 0, False)),
        httpx.Response(200, json=page([{"id": "3"}, {"id": "4"}], 2, False)),
        httpx.Response(200, json=page([{"id": "5"}], 4, True)),
    ]

    histories = client.fetch_changelog("SBX-6")

    assert [h["id"] for h in histories] == ["1", "2", "3", "4", "5"]
    assert [c.request.url.params["startAt"] for c in route.calls] == ["0", "2", "4"]


@respx.mock
def test_ac2_empty_page_stops_paging(client: JiraClient) -> None:
    route = respx.get(f"{API}/group/member")
    route.side_effect = [
        httpx.Response(200, json=page([{"accountId": "user-01"}], 0, False)),
        httpx.Response(200, json=page([], 1, False)),
    ]

    assert client.group_members("approvers") == {"user-01"}
    assert route.calls.last.request.url.params["groupname"] == "approvers"


@respx.mock
def test_ac2_comments_page_by_total(client: JiraClient) -> None:
    route = respx.get(f"{API}/issue/SBX-6/comment")
    route.side_effect = [
        httpx.Response(
            200, json={"startAt": 0, "maxResults": 1, "total": 2, "comments": [{"id": "a"}]}
        ),
        httpx.Response(
            200, json={"startAt": 1, "maxResults": 1, "total": 2, "comments": [{"id": "b"}]}
        ),
    ]

    assert [c["id"] for c in client.fetch_comments("SBX-6")] == ["a", "b"]
    assert route.call_count == 2


# --- AC3: 429 with Retry-After -----------------------------------------------------------------


@respx.mock
def test_ac3_retry_after_is_honoured(client: JiraClient, waits: list[float]) -> None:
    respx.get(f"{API}/issue/SBX-1").side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"key": "SBX-1"}),
    ]

    assert client.fetch_issue("SBX-1") == {"key": "SBX-1"}
    assert waits == [7.0]


@respx.mock
def test_ac3_repeated_429_gives_source_unavailable(client: JiraClient, waits: list[float]) -> None:
    route = respx.get(f"{API}/issue/SBX-1").respond(429)

    with pytest.raises(SourceUnavailable, match="429") as raised:
        client.fetch_issue("SBX-1")

    assert raised.value.exit_code == 3
    assert raised.value.retryable
    assert route.call_count == 4
    assert waits == [2.0, 4.0, 8.0]  # no Retry-After: exponential backoff


# --- AC4: 5xx and timeouts ---------------------------------------------------------------------


@respx.mock
def test_ac4_server_error_is_retried_then_succeeds(client: JiraClient, waits: list[float]) -> None:
    respx.get(f"{API}/issue/SBX-1").side_effect = [
        httpx.Response(503),
        httpx.Response(200, json={"key": "SBX-1"}),
    ]

    assert client.fetch_issue("SBX-1")["key"] == "SBX-1"
    assert waits == [2.0]


@respx.mock
def test_ac4_persistent_500_gives_source_unavailable(client: JiraClient) -> None:
    respx.get(f"{API}/issue/SBX-1").respond(500)

    with pytest.raises(SourceUnavailable, match="HTTP 500") as raised:
        client.fetch_issue("SBX-1")

    assert raised.value.status == 500


@respx.mock
def test_ac4_timeout_gives_source_unavailable(client: JiraClient, waits: list[float]) -> None:
    respx.get(f"{API}/issue/SBX-1").side_effect = httpx.ReadTimeout("slow")

    with pytest.raises(SourceUnavailable, match="slow") as raised:
        client.fetch_issue("SBX-1")

    assert raised.value.status is None
    assert waits == [2.0, 4.0, 8.0]


@respx.mock
def test_ac4_connection_error_then_success(client: JiraClient) -> None:
    respx.get(f"{API}/issue/SBX-1").side_effect = [
        httpx.ConnectError("refused"),
        httpx.Response(200, json={"key": "SBX-1"}),
    ]

    assert client.fetch_issue("SBX-1")["key"] == "SBX-1"


def test_ac4_jitter_scales_the_backoff() -> None:
    waits: list[float] = []
    client = JiraClient(BASE, "e", "t", sleep=waits.append, jitter=lambda: 0.5)
    with respx.mock:
        respx.get(f"{API}/issue/SBX-1").respond(502)
        with pytest.raises(SourceUnavailable):
            client.fetch_issue("SBX-1")

    assert waits == [1.0, 2.0, 4.0]


# --- AC5: 401 and 404 ----------------------------------------------------------------------------


@respx.mock
def test_ac5_wrong_token_is_not_retried(client: JiraClient, waits: list[float]) -> None:
    route = respx.get(f"{API}/issue/SBX-1").respond(401, text="Unauthorized")

    with pytest.raises(CollectionError) as raised:
        client.fetch_issue("SBX-1")

    error = raised.value
    assert (error.status, error.retryable, error.exit_code) == (401, False, 3)
    assert not isinstance(error, SourceUnavailable)
    assert route.call_count == 1
    assert waits == []
    assert "token" not in str(error)


@respx.mock
def test_ac5_missing_issue_gives_404_with_jira_message(client: JiraClient) -> None:
    respx.get(f"{API}/issue/SBX-99").respond(
        404,
        json={"errorMessages": ["Issue does not exist or you do not have permission to see it."]},
    )

    with pytest.raises(CollectionError, match="Issue does not exist") as raised:
        client.fetch_issue("SBX-99")

    assert raised.value.status == 404


@respx.mock
def test_body_that_is_not_json_is_a_collection_error(client: JiraClient) -> None:
    respx.get(f"{API}/issue/SBX-1").respond(200, text="<html>maintenance</html>")

    with pytest.raises(CollectionError, match="not JSON"):
        client.fetch_issue("SBX-1")


def test_client_closes(client: JiraClient) -> None:
    with client:
        pass


@respx.mock
def test_ac2_is_last_alone_stops_paging(client: JiraClient) -> None:
    # No `total` in the page: only `isLast` says there is nothing more to fetch.
    route = respx.get(f"{API}/issue/SBX-6/changelog")
    route.side_effect = [
        httpx.Response(200, json={"startAt": 0, "isLast": True, "values": [{"id": "1"}]}),
        httpx.Response(200, json={"startAt": 1, "isLast": True, "values": [{"id": "2"}]}),
    ]

    assert [h["id"] for h in client.fetch_changelog("SBX-6")] == ["1"]
    assert route.call_count == 1
