"""Read-only GitHub REST client (spec §6 `collect.github`, step-9).

Fetches the three resources CodeAtlas needs for one pull request:
  - the pull request itself (base + head SHAs, title, body, labels, author),
  - its commits (sha, author email, login, committed_at, message),
  - its reviews (reviewer, state, commit_id, submitted_at).

Also pulls the base-commit `CODEOWNERS` as raw text — the governance snapshot
in Branch 3 parses it. No other side effects: GET only, no writes.

Rate-limit handling: on `X-RateLimit-Remaining: 0`, sleep until
`X-RateLimit-Reset`. On 429, honour `Retry-After` once; if it happens twice in
one call, raise `CollectionError`. On 5xx, retry with exponential backoff
(1s, 2s, 4s), then raise.

Pagination: GitHub returns up to 100 items per page; we page until the `Link`
header has no `rel="next"`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from codeatlas.collect.github._errors import CollectionError

DEFAULT_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT_S = 30.0
PER_PAGE = 100
MAX_PAGES = 100  # 10,000 items; nothing in our sandbox will approach this
MAX_5XX_RETRIES = 3


@dataclass(frozen=True)
class GitHubClientConfig:
    """Immutable client config. Pass this instead of positional args."""

    token: str
    api_base: str = DEFAULT_API_BASE
    timeout_s: float = DEFAULT_TIMEOUT_S
    user_agent: str = "codeatlas/0.1 (+https://github.com/codeatlas-fyp/codeatlas)"


class GitHubClient:
    """Thin read-only wrapper around GitHub REST v3.

    All methods return the raw JSON GitHub sent — the recorder sanitises it, and
    `analyze/` turns it into `Commit`, `Review`, `WorkItemRef` evidence objects.
    """

    def __init__(self, config: GitHubClientConfig) -> None:
        self._config = config
        self._session: httpx.Client | None = None

    # ---- public surface --------------------------------------------------

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        """GET /repos/{repo}/pulls/{number} — PR metadata (base/head SHAs, title, body)."""
        return self._get_json(f"/repos/{repo}/pulls/{number}")

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        """GET /repos/{repo}/pulls/{number}/commits — all commits, oldest first."""
        return self._get_paged(f"/repos/{repo}/pulls/{number}/commits")

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        """GET /repos/{repo}/pulls/{number}/reviews — includes `commit_id` per review (C5 needs this)."""
        return self._get_paged(f"/repos/{repo}/pulls/{number}/reviews")

    def file_at_commit(self, repo: str, path: str, ref: str) -> str | None:
        """GET /repos/{repo}/contents/{path}?ref={ref} — raw file text at a commit.

        Returns None if the file is absent at that ref (404), which is how a
        missing CODEOWNERS at the base commit is signalled.
        """
        url = f"/repos/{repo}/contents/{path}?{urlencode({'ref': ref})}"
        try:
            data = self._get_json(url, accept="application/vnd.github.raw")
        except CollectionError as error:
            if error.status == 404:
                return None
            raise
        return data if isinstance(data, str) else None

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---- plumbing --------------------------------------------------------

    def _session_(self) -> httpx.Client:
        if self._session is None:
            headers = {
                "Authorization": f"Bearer {self._config.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": self._config.user_agent,
                "X-GitHub-Api-Version": "2022-11-28",
            }
            self._session = httpx.Client(
                base_url=self._config.api_base,
                headers=headers,
                timeout=self._config.timeout_s,
            )
        return self._session

    def _get_json(self, path: str, *, accept: str | None = None) -> Any:
        """One GET with retry on 5xx, honour on 429, sleep on rate exhaustion."""
        headers: dict[str, str] = {}
        if accept is not None:
            headers["Accept"] = accept

        for attempt in range(MAX_5XX_RETRIES + 1):
            resp = self._session_().get(path, headers=headers)

            # Rate-limit prevention: GitHub tells us how many requests remain.
            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining == "0" and resp.status_code != 200:
                reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
                wait = max(0, reset - int(time.time())) + 1
                time.sleep(min(wait, 60))

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                time.sleep(min(retry_after, 60))
                continue

            if 500 <= resp.status_code < 600 and attempt < MAX_5XX_RETRIES:
                time.sleep(2**attempt)
                continue

            if resp.status_code >= 400:
                raise CollectionError(
                    source="github",
                    status=resp.status_code,
                    retryable=resp.status_code in (502, 503, 504),
                    message=f"GET {path} -> {resp.status_code}: {resp.text[:200]}",
                )

            if accept == "application/vnd.github.raw":
                return resp.text
            return resp.json()

        raise CollectionError(
            source="github", status=None, retryable=True,
            message=f"GET {path} exceeded retries",
        )

    def _get_paged(self, path: str) -> list[dict[str, Any]]:
        """Follow `Link: rel=next` until exhausted."""
        results: list[dict[str, Any]] = []
        next_path: str | None = f"{path}?per_page={PER_PAGE}"
        pages = 0
        while next_path is not None and pages < MAX_PAGES:
            resp = self._session_().get(next_path)
            if resp.status_code >= 400:
                raise CollectionError(
                    source="github",
                    status=resp.status_code,
                    retryable=False,
                    message=f"GET {next_path} -> {resp.status_code}",
                )
            page = resp.json()
            if not isinstance(page, list):
                break
            results.extend(page)
            next_path = _next_link(resp.headers.get("Link", ""))
            pages += 1
        return results


def client_from_env() -> GitHubClient:
    """Build a client from `CODEATLAS_GITHUB_TOKEN`. Raises if the token is missing."""
    token = os.environ.get("CODEATLAS_GITHUB_TOKEN", "")
    if not token:
        raise CollectionError(
            source="github", status=None, retryable=False,
            message="CODEATLAS_GITHUB_TOKEN is not set",
        )
    return GitHubClient(GitHubClientConfig(token=token))


def _next_link(link_header: str) -> str | None:
    """Parse the Link header's rel=next URL. Returns the path with query only."""
    if not link_header:
        return None
    for part in link_header.split(","):
        chunks = [c.strip() for c in part.split(";")]
        if 'rel="next"' not in chunks:
            continue
        url_chunk = chunks[0]
        if url_chunk.startswith("<") and url_chunk.endswith(">"):
            url = url_chunk[1:-1]
            # Strip base so httpx uses the client's base_url.
            if "://" in url:
                _, _, tail = url.partition("://")
                _, _, path_and_query = tail.partition("/")
                return "/" + path_and_query
            return url
    return None
