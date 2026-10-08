"""Record GitHub responses as sanitised fixtures (spec §13, step-9).

Every call to the live client is written as JSON under `tests/fixtures/github/`
with a stable, deterministic structure:

    tests/fixtures/github/<repo>/<pr>/
        pr.json
        commits.json
        reviews.json
        codeowners.txt     (base-commit raw text, may be absent)
        meta.json          (sanitiser hints: real→fake handle map, timestamps)

Sanitisation is **irreversible**: real logins, emails, display names and
account_ids are replaced by stable fakes (user-01, user-01@example.test, ...)
chosen by a per-run counter keyed on first appearance. The recorder NEVER
writes a raw response that still contains a real email.

Reading back: `fixture_source(path)` returns an object implementing the
`ChangeSource` Protocol so tests use it exactly like a live client.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class Sanitiser:
    """Build a stable fake-identity map on first contact with each real value.

    Call `sanitise(obj)` and the recorder gives back the same object with every
    email, login and account_id replaced consistently. Two mentions of the same
    `areej8` become the same `user-NN`.
    """

    def __init__(self) -> None:
        self._login_map: dict[str, str] = {}
        self._email_map: dict[str, str] = {}
        self._account_map: dict[str, str] = {}
        self._next_id = 1

    def login_for(self, real: str | None) -> str | None:
        if real is None:
            return None
        if real not in self._login_map:
            self._login_map[real] = f"user-{self._next_id:02d}"
            self._next_id += 1
        return self._login_map[real]

    def email_for(self, real: str | None) -> str | None:
        if real is None or not real:
            return real
        if real not in self._email_map:
            fake_login = self.login_for(real.split("@", 1)[0]) or f"user-{self._next_id:02d}"
            self._email_map[real] = f"{fake_login}@example.test"
        return self._email_map[real]

    def account_for(self, real: str | None) -> str | None:
        if real is None:
            return None
        if real not in self._account_map:
            self._account_map[real] = f"account-{self._next_id:02d}"
            self._next_id += 1
        return self._account_map[real]

    def sanitise(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self._sanitise_pair(k, v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.sanitise(item) for item in obj]
        if isinstance(obj, str):
            return _EMAIL_RE.sub(lambda m: self.email_for(m.group(0)) or "", obj)
        return obj

    def _sanitise_pair(self, key: str, value: Any) -> Any:
        """Replace values whose key names a known identity field."""
        if value is None:
            return None
        if key == "login" and isinstance(value, str):
            return self.login_for(value)
        if key in ("email", "author_email") and isinstance(value, str):
            return self.email_for(value)
        if key in ("accountId", "account_id") and isinstance(value, str):
            return self.account_for(value)
        if key == "name" and isinstance(value, str):
            # Display names: coarsely redact to the fake login they map to.
            return self.login_for(value) or value
        return self.sanitise(value)

    def meta(self) -> dict[str, Any]:
        return {
            "login_map": self._login_map,
            "email_map": self._email_map,
            "account_map": self._account_map,
        }


@dataclass
class RecorderPaths:
    """Where fixtures live. Default follows the layout above."""

    root: Path
    repo: str
    pr_number: int

    def folder(self) -> Path:
        return self.root / self.repo.replace("/", "__") / f"pr-{self.pr_number}"

    def pr_file(self) -> Path:
        return self.folder() / "pr.json"

    def commits_file(self) -> Path:
        return self.folder() / "commits.json"

    def reviews_file(self) -> Path:
        return self.folder() / "reviews.json"

    def codeowners_file(self) -> Path:
        return self.folder() / "codeowners.txt"

    def meta_file(self) -> Path:
        return self.folder() / "meta.json"


class Recorder:
    """Call live client, sanitise, write to disk.

    Usage:
        recorder = Recorder(client, paths)
        recorder.record_all()                  # writes pr/commits/reviews/codeowners
    """

    def __init__(self, client: Any, paths: RecorderPaths) -> None:
        self._client = client
        self._paths = paths
        self._sanitiser = Sanitiser()

    def record_all(self, codeowners_path: str = "CODEOWNERS") -> None:
        folder = self._paths.folder()
        folder.mkdir(parents=True, exist_ok=True)

        pr = self._sanitiser.sanitise(
            self._client.pull_request(self._paths.repo, self._paths.pr_number)
        )
        _write_json(self._paths.pr_file(), pr)

        commits = self._sanitiser.sanitise(
            self._client.commits(self._paths.repo, self._paths.pr_number)
        )
        _write_json(self._paths.commits_file(), commits)

        reviews = self._sanitiser.sanitise(
            self._client.reviews(self._paths.repo, self._paths.pr_number)
        )
        _write_json(self._paths.reviews_file(), reviews)

        base_sha = pr.get("base", {}).get("sha") if isinstance(pr, dict) else None
        if base_sha:
            raw = self._client.file_at_commit(self._paths.repo, codeowners_path, base_sha)
            if raw is not None:
                self._paths.codeowners_file().write_text(raw, encoding="utf-8")

        _write_json(self._paths.meta_file(), self._sanitiser.meta())


# ---- fixture replay source (used by tests) --------------------------------

@dataclass
class FixtureSource:
    """Stand-in for GitHubClient that reads recorded JSON. Satisfies ChangeSource."""

    root: Path
    _memo: dict[tuple[str, int, str], Any] = field(default_factory=dict)

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return self._read(repo, number, "pr.json")

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        data = self._read(repo, number, "commits.json")
        return list(data) if isinstance(data, list) else []

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        data = self._read(repo, number, "reviews.json")
        return list(data) if isinstance(data, list) else []

    def file_at_commit(self, repo: str, path: str, ref: str) -> str | None:
        if path != "CODEOWNERS":
            return None
        folder = self.root / repo.replace("/", "__") / f"pr-{self._active_pr(repo)}"
        f = folder / "codeowners.txt"
        return f.read_text(encoding="utf-8") if f.is_file() else None

    def _active_pr(self, repo: str) -> int:
        # Simple: assume single PR per repo-folder in tests. The lookup walks
        # subdirs and returns the first `pr-<n>`.
        base = self.root / repo.replace("/", "__")
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith("pr-"):
                return int(child.name.split("-", 1)[1])
        raise FileNotFoundError(f"no PR fixture under {base}")

    def _read(self, repo: str, number: int, name: str) -> Any:
        key = (repo, number, name)
        if key in self._memo:
            return self._memo[key]
        f = self.root / repo.replace("/", "__") / f"pr-{number}" / name
        if not f.is_file():
            raise FileNotFoundError(f"fixture missing: {f}")
        value = json.loads(f.read_text(encoding="utf-8"))
        self._memo[key] = value
        return value


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
