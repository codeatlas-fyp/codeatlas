"""The only code in the repository that writes to Jira (CLAUDE.md task authority).

Guards, each checked before any request:
- the project key must be exactly `SBX`;
- every issue it edits or transitions must carry the label `generated`;
- it never touches SBX-1..SBX-7 (the hand-made demo issues).

Endpoints (Jira Cloud REST v3, used as documented): create `POST /issue`, edit `PUT /issue/{key}`,
transitions `GET/POST /issue/{key}/transitions`. Rate limits: a pause between writes, and 429
answers wait `Retry-After` seconds (Atlassian rate-limiting guide).
"""

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

PROJECT = "SBX"
LABEL = "generated"
PROTECTED = {f"SBX-{n}" for n in range(1, 8)}


class RefusedError(RuntimeError):
    """A write the generator must never make."""


def adf(text: str) -> dict[str, Any]:
    """Plain text as an ADF document, one paragraph per line."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": line}]}
            for line in text.split("\n")
        ],
    }


class JiraWriter:
    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        project: str,
        pause: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if project != PROJECT:
            raise RefusedError(f"the scenario generator only writes to {PROJECT}, not {project!r}")
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/rest/api/3",
            auth=(email, token),
            headers={"Accept": "application/json"},
            timeout=30.0,
            transport=transport,
        )
        self._pause = pause
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _send(self, method: str, path: str, body: Any = None) -> httpx.Response:
        for _ in range(5):
            response = self._http.request(method, path, json=body)
            if response.status_code != 429:
                response.raise_for_status()
                if method != "GET":
                    self._sleep(self._pause)
                return response
            self._sleep(float(response.headers.get("Retry-After", "5")))
        raise RuntimeError(f"{method} {path}: still rate-limited after 5 attempts")

    def myself(self) -> str:
        return str(self._send("GET", "/myself").json()["accountId"])

    def create_issue(self, summary: str, description: str, run_label: str) -> str:
        fields = {
            "project": {"key": PROJECT},
            "issuetype": {"name": "Story"},
            "summary": summary,
            "description": adf(description),
            "labels": [LABEL, run_label],
        }
        key = str(self._send("POST", "/issue", {"fields": fields}).json()["key"])
        self._check_generated(key)
        return key

    def _check_generated(self, key: str) -> None:
        if not re.fullmatch(rf"{PROJECT}-\d+", key) or key in PROTECTED:
            raise RefusedError(f"{key} is not a generated {PROJECT} issue")
        labels = self._send("GET", f"/issue/{key}", None).json()["fields"].get("labels") or []
        if LABEL not in labels:
            raise RefusedError(f"{key} is not labelled {LABEL!r}; refusing to change it")

    def edit_description(self, key: str, text: str) -> None:
        self._check_generated(key)
        self._send("PUT", f"/issue/{key}", {"fields": {"description": adf(text)}})

    def set_priority(self, key: str, priority: str) -> None:
        self._check_generated(key)
        self._send("PUT", f"/issue/{key}", {"fields": {"priority": {"name": priority}}})

    def move_to(self, key: str, status: str) -> None:
        self._check_generated(key)
        transitions = self._send("GET", f"/issue/{key}/transitions").json()["transitions"]
        match = [t for t in transitions if t["to"]["name"] == status]
        if not match:
            names = sorted(t["to"]["name"] for t in transitions)
            raise RefusedError(f"{key}: no transition into {status!r} (available: {names})")
        self._send("POST", f"/issue/{key}/transitions", {"transition": {"id": match[0]["id"]}})

    def changelog(self, key: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self._send(
                "GET", f"/issue/{key}/changelog?startAt={start}&maxResults=100"
            ).json()
            values += page.get("values") or []
            start = len(values)
            if page.get("isLast", True) or not page.get("values"):
                return values
