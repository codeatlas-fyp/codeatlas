"""Read-only Jira Cloud REST v3 client (step-5 spec; spec §6 `collect.jira`, §16).

Returns raw JSON so it can be recorded as evidence. Every failure becomes a `CollectionError`;
nothing above `collect/` sees an httpx exception. Retries follow Atlassian's rate-limiting guide:
honour `Retry-After` on 429, otherwise exponential backoff from 2 s with jitter, 4 attempts.
"""

import random
import re
import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any, Self

import httpx

from codeatlas.errors import CollectionError, SourceUnavailable

SOURCE = "jira"
PAGE_SIZE = 100
BACKOFF_BASE_SECONDS = 2.0
_KEY = re.compile(r"[A-Z][A-Z0-9_]*-\d+")


class ReadOnlyTransport(httpx.BaseTransport):
    """Refuses every non-GET request before it leaves the machine (CodeAtlas is read-only)."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise CollectionError(
                SOURCE,
                status=None,
                retryable=False,
                detail=f"read-only client refused {request.method}",
            )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def _jitter() -> float:
    return random.uniform(0.7, 1.3)  # noqa: S311 (timing jitter, not security)


class JiraClient:
    """`WorkItemSource` over live Jira Cloud."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = _jitter,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/api/3",
            auth=(email, token),
            headers={"Accept": "application/json"},
            timeout=timeout,
            transport=ReadOnlyTransport(transport or httpx.HTTPTransport()),
        )
        self._sleep = sleep
        self._jitter = jitter
        self._max_retries = max_retries

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def fetch_issue(self, key: str) -> dict[str, Any]:
        """The issue with all fields; rich text stays ADF."""
        return self._get(f"/issue/{_checked(key)}", {"fields": "*all"})

    def fetch_changelog(self, key: str) -> list[dict[str, Any]]:
        """Every changelog history, all pages, in server order (oldest first)."""
        return list(self._pages(f"/issue/{_checked(key)}/changelog", {}, "values"))

    def fetch_comments(self, key: str) -> list[dict[str, Any]]:
        return list(self._pages(f"/issue/{_checked(key)}/comment", {}, "comments"))

    def group_members(self, group: str) -> set[str]:
        """Account ids of the group's current members (Jira keeps no membership history)."""
        members = self._pages("/group/member", {"groupname": group}, "values")
        return {str(member["accountId"]) for member in members if "accountId" in member}

    def _pages(self, path: str, params: dict[str, Any], key: str) -> Iterator[dict[str, Any]]:
        start = 0
        while True:
            page = self._get(path, {**params, "startAt": start, "maxResults": PAGE_SIZE})
            values = page.get(key) or []
            yield from values
            start += len(values)
            total = page.get("total")
            if page.get("isLast") is True or not values:
                return
            if isinstance(total, int) and start >= total:
                return

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as error:
                if last:
                    raise SourceUnavailable(
                        SOURCE, status=None, retryable=True, detail=str(error)
                    ) from error
                self._wait(attempt, None)
                continue
            status = response.status_code
            if status == 429 or status >= 500:
                if last:
                    raise SourceUnavailable(
                        SOURCE, status=status, retryable=True, detail="retries exhausted"
                    )
                self._wait(attempt, response.headers.get("Retry-After"))
                continue
            if status >= 400:
                raise CollectionError(
                    SOURCE, status=status, retryable=False, detail=_jira_message(response)
                )
            try:
                data = response.json()
            except ValueError as error:
                raise CollectionError(
                    SOURCE, status=status, retryable=False, detail="response is not JSON"
                ) from error
            if not isinstance(data, dict):
                raise CollectionError(
                    SOURCE, status=status, retryable=False, detail="response is not an object"
                )
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        try:
            seconds = float(retry_after) if retry_after is not None else -1.0
        except ValueError:
            seconds = -1.0
        if seconds < 0:
            seconds = BACKOFF_BASE_SECONDS * 2**attempt * self._jitter()
        self._sleep(seconds)


def _checked(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise ValueError(f"{key!r} is not a Jira issue key")
    return key


def _jira_message(response: httpx.Response) -> str:
    """Jira's own error text, never the request (which carries credentials)."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(body, dict):
        messages = body.get("errorMessages") or []
        if isinstance(messages, list) and messages:
            return "; ".join(str(m) for m in messages)
    return response.reason_phrase
