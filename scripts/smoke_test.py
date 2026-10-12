"""Smoke-test a running CodeAtlas web service (spec §12 CD: "smoke test checks /health and replay").

Used by CD twice: against the freshly built container inside GitHub Actions, and against the
live Render URL after a deploy. Standard library only, so it runs without installing anything.

    python scripts/smoke_test.py http://127.0.0.1:8000 --version 0.1.0 --commit <sha>

Basic-auth credentials come from SMOKE_USER / SMOKE_PASSWORD (never from arguments, so they
do not appear in logs). Exit code 0 = every check passed, 1 = a check failed.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class SmokeError(Exception):
    pass


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Client:
    base_url: str
    user: str | None = None
    password: str | None = None
    timeout: float = 15.0
    log: list[str] = field(default_factory=list)

    def get(self, path: str, auth: bool = True) -> Response:
        url = self.base_url.rstrip("/") + path
        if not url.startswith(("http://", "https://")):
            raise SmokeError(f"refusing non-HTTP URL {url!r}")
        request = urllib.request.Request(url, headers={"Accept": "*/*"})  # noqa: S310
        if auth and self.user and self.password:
            token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as r:  # noqa: S310
                return Response(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read())


def wait_for_health(client: Client, attempts: int, delay: float) -> Response:
    """Poll /health until it answers 200 (containers and free hosts need time to boot)."""
    last = "no response"
    for attempt in range(1, attempts + 1):
        try:
            response = client.get("/health", auth=False)
            if response.status == 200:
                return response
            last = f"HTTP {response.status}"
        except (urllib.error.URLError, OSError) as e:
            last = str(e)
        if attempt < attempts:
            time.sleep(delay)
    raise SmokeError(f"/health never returned 200 after {attempts} attempts ({last})")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def run(
    client: Client,
    expect_version: str | None = None,
    expect_commit: str | None = None,
    attempts: int = 30,
    delay: float = 2.0,
) -> list[str]:
    """Run every check; return one line per passed check, raise SmokeError on the first failure."""
    passed: list[str] = []

    health = wait_for_health(client, attempts, delay).json()
    check(health.get("status") == "ok", f"/health status is {health.get('status')!r}")
    if expect_version:
        check(
            health.get("version") == expect_version,
            f"/health version {health.get('version')!r} != expected {expect_version!r}",
        )
    if expect_commit:
        check(
            health.get("commit") == expect_commit,
            f"/health commit {health.get('commit')!r} != expected {expect_commit!r}",
        )
    passed.append(f"/health ok (version {health.get('version')}, commit {health.get('commit')})")

    if client.user and client.password:
        anonymous = client.get("/api/cases", auth=False)
        check(anonymous.status == 401, f"/api/cases without login gave {anonymous.status}, not 401")
        passed.append("basic auth: anonymous request refused with 401")

    cases_response = client.get("/api/cases")
    check(cases_response.status == 200, f"/api/cases gave HTTP {cases_response.status}")
    cases = cases_response.json()
    check(isinstance(cases, list) and len(cases) > 0, "/api/cases returned no cases")
    passed.append(f"/api/cases lists {len(cases)} case(s)")

    healthy = [c for c in cases if not c.get("corrupt")]
    check(len(healthy) > 0, "every stored bundle failed its integrity check")
    bundle_hash = healthy[0]["bundle_hash"]

    replay = client.get(f"/api/cases/{bundle_hash}/replay")
    check(replay.status == 200, f"replay gave HTTP {replay.status}")
    outcome = replay.json()
    check(
        outcome.get("match") is True and outcome.get("refused") is False,
        f"replay of {bundle_hash[:12]} did not match: {outcome.get('message')}",
    )
    passed.append(f"replay of {bundle_hash[:12]} reproduces the stored verdict")

    page = client.get("/")
    check(page.status == 200, f"case list page gave HTTP {page.status}")
    check(b"CodeAtlas" in page.body, "case list page does not mention CodeAtlas")
    check(
        page.headers.get("x-content-type-options") == "nosniff",
        "security headers missing on HTML pages",
    )
    passed.append("case list page renders with security headers")

    detail = client.get(f"/cases/{bundle_hash}")
    check(detail.status == 200, f"case page gave HTTP {detail.status}")
    passed.append("case detail page renders")

    missing = client.get("/api/cases/" + "0" * 64)
    check(missing.status == 404, f"unknown case gave HTTP {missing.status}, not 404")
    check("error" in missing.json(), "unknown case did not use the error shape")
    passed.append("unknown case returns the 404 error shape")

    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--version", dest="expect_version", help="expected /health version")
    parser.add_argument("--commit", dest="expect_commit", help="expected /health commit")
    parser.add_argument("--attempts", type=int, default=30, help="/health polls before giving up")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between polls")
    args = parser.parse_args(argv)

    client = Client(
        args.url,
        user=os.environ.get("SMOKE_USER") or None,
        password=os.environ.get("SMOKE_PASSWORD") or None,
    )
    try:
        for line in run(client, args.expect_version, args.expect_commit, args.attempts, args.delay):
            print(f"PASS  {line}")
    except (SmokeError, urllib.error.URLError, OSError, ValueError) as e:
        print(f"FAIL  {e}")
        return 1
    print(f"Smoke test passed for {args.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
