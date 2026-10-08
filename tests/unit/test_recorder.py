"""Recorder tests. Sanitiser is tested stand-alone; FixtureSource round-trips."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codeatlas.collect.github.recorder import (
    FixtureSource,
    Recorder,
    RecorderPaths,
    Sanitiser,
)


class CannedClient:
    """Pretend client that returns pre-made responses for the recorder."""

    def __init__(self, pr: dict[str, Any], commits: list[dict[str, Any]],
                 reviews: list[dict[str, Any]], codeowners: str | None) -> None:
        self._pr = pr
        self._commits = commits
        self._reviews = reviews
        self._codeowners = codeowners

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return self._pr

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._commits

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._reviews

    def file_at_commit(self, repo: str, path: str, ref: str) -> str | None:
        return self._codeowners if path == "CODEOWNERS" else None


def test_sanitiser_replaces_logins_consistently() -> None:
    s = Sanitiser()
    first = s.login_for("areej8")
    second = s.login_for("areej8")
    assert first == second
    assert first == "user-01"


def test_sanitiser_strips_emails_from_free_text() -> None:
    s = Sanitiser()
    sanitised = s.sanitise("contact areejhamid8560@gmail.com for details")
    assert "areejhamid8560" not in sanitised
    assert "@example.test" in sanitised


def test_sanitiser_rewrites_nested_logins_and_emails() -> None:
    s = Sanitiser()
    before = {
        "user": {"login": "areej8", "email": "areej8@example.com"},
        "commits": [{"commit": {"author": {"email": "yusra@example.com"}}}],
    }
    after = s.sanitise(before)
    assert after["user"]["login"] == "user-01"
    assert after["user"]["email"] == "user-01@example.test"
    assert after["commits"][0]["commit"]["author"]["email"] == "user-02@example.test"


def test_recorder_round_trip(tmp_path: Path) -> None:
    pr = {
        "number": 42,
        "title": "SBX-4: late fee",
        "body": "closes SBX-4",
        "base": {"sha": "base123"},
        "head": {"sha": "head456"},
        "user": {"login": "YusraM-100", "email": "yusra@example.com"},
    }
    commits = [
        {
            "sha": "head456",
            "commit": {
                "message": "feat: late fee calc (SBX-4)",
                "author": {"email": "yusra@example.com"},
            },
        }
    ]
    reviews = [
        {"user": {"login": "areej8"}, "state": "APPROVED", "commit_id": "head456",
         "submitted_at": "2026-10-05T12:00:00Z"}
    ]
    codeowners = "* @YusraM-100\n/payments/ @areej8\n"
    client = CannedClient(pr, commits, reviews, codeowners)

    paths = RecorderPaths(root=tmp_path, repo="codeatlas-fyp/codeatlas-sandbox", pr_number=42)
    recorder = Recorder(client, paths)
    recorder.record_all()

    # Fixtures exist, logins sanitised, codeowners written as-is (sanitiser does
    # not rewrite CODEOWNERS text — that is intentional, since teams compare
    # against literal handles in demos).
    saved_pr = json.loads(paths.pr_file().read_text())
    assert saved_pr["user"]["login"] == "user-01"
    assert paths.codeowners_file().read_text() == codeowners

    # FixtureSource reads them back.
    source = FixtureSource(root=tmp_path)
    assert source.pull_request("codeatlas-fyp/codeatlas-sandbox", 42)["number"] == 42
    assert source.commits("codeatlas-fyp/codeatlas-sandbox", 42)[0]["sha"] == "head456"
    assert source.reviews("codeatlas-fyp/codeatlas-sandbox", 42)[0]["commit_id"] == "head456"
    assert source.file_at_commit("codeatlas-fyp/codeatlas-sandbox", "CODEOWNERS", "base123") == codeowners
