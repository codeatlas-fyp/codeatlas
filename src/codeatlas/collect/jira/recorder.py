"""Record Jira responses as test fixtures, with personal data replaced (spec §8, §13, §16).

Fixtures are produced here and only here: recorded from live Jira, sanitised, written as JSON.
`FixtureJiraSource` reads them back as a `WorkItemSource`, so tests never touch the network.
"""

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from codeatlas.collect.jira.client import status_categories
from codeatlas.errors import CollectionError
from codeatlas.schema import WorkItemSource

FAKE_HOST = "example.atlassian.net"
_PERSON_KEYS = ("accountId", "displayName", "emailAddress")


class Sanitiser:
    """Replace people and the site host with consistent fakes.

    Every Jira user object (a dict with `accountId`) is learned first; then every string in the
    data has that person's account id, display name and email replaced, so references inside
    changelog items (`from`, `toString`, ...) and free text are caught too. Person N becomes
    `user-NN`, `User NN`, `user-NN@example.test`.
    """

    def __init__(self, site_host: str, known: dict[str, int] | None = None) -> None:
        self._site_host = site_host
        self._numbers: dict[str, int] = dict(known or {})
        self._replacements: dict[str, str] = {}

    def mapping(self) -> dict[str, int]:
        """Account id → person number; pass it as `known` to keep fakes stable across runs."""
        return dict(self._numbers)

    def sanitise(self, data: Any) -> Any:
        for person in _people(data):
            self._learn(person)
        return self._replace(data)

    def _learn(self, person: dict[str, Any]) -> None:
        account = str(person["accountId"])
        number = self._numbers.setdefault(account, len(self._numbers) + 1)
        fake = {
            "accountId": f"user-{number:02d}",
            "displayName": f"User {number:02d}",
            "emailAddress": f"user-{number:02d}@example.test",
        }
        for key in _PERSON_KEYS:
            value = person.get(key)
            if isinstance(value, str) and value:
                self._replacements[value] = fake[key]

    def _replace(self, value: Any) -> Any:
        if isinstance(value, dict):
            out = {key: self._replace(item) for key, item in value.items()}
            if "accountId" in value and isinstance(value.get("avatarUrls"), dict):
                fake_id = out["accountId"]
                out["avatarUrls"] = {
                    size: f"https://{FAKE_HOST}/avatar/{fake_id}/{size}"
                    for size in value["avatarUrls"]
                }
            return out
        if isinstance(value, list):
            return [self._replace(item) for item in value]
        if isinstance(value, str):
            return self._replace_text(value)
        return value

    def _replace_text(self, text: str) -> str:
        for real in sorted(self._replacements, key=len, reverse=True):
            if real in text:
                text = text.replace(real, self._replacements[real])
        return text.replace(self._site_host, FAKE_HOST)


def _people(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("accountId"), str):
            yield value
        for item in value.values():
            yield from _people(item)
    elif isinstance(value, list):
        for item in value:
            yield from _people(item)


def _write(path: Path, data: Any) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def record(
    source: Any, keys: Iterable[str], out_dir: Path, sanitiser: Sanitiser | None = None
) -> list[Path]:
    """Write `<KEY>.issue.json`, `.changelog.json`, `.comments.json` for each key, and
    `<PROJECT>.statuses.json` for each project the keys belong to.

    `source` is a `JiraClient`; with no sanitiser the raw responses are written (only ever into
    the git-ignored `.recordings/` folder).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = list(keys)
    written = []
    for key in keys:
        responses = {
            "issue": source.fetch_issue(key),
            "changelog": source.fetch_changelog(key),
            "comments": source.fetch_comments(key),
        }
        for kind, data in responses.items():
            path = out_dir / f"{key}.{kind}.json"
            _write(path, sanitiser.sanitise(data) if sanitiser else data)
            written.append(path)
    for project in sorted({key.split("-")[0] for key in keys}):
        data = source.fetch_project_statuses(project)
        path = out_dir / f"{project}.statuses.json"
        _write(path, sanitiser.sanitise(data) if sanitiser else data)
        written.append(path)
    return written


class FixtureJiraSource:
    """`WorkItemSource` that reads recorded files; a missing file behaves like a Jira 404."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def _read(self, name: str) -> Any:
        path = self._folder / name
        if not path.is_file():
            raise CollectionError(
                "jira", status=404, retryable=False, detail=f"no recording {name}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def fetch_issue(self, key: str) -> dict[str, Any]:
        data: dict[str, Any] = self._read(f"{key}.issue.json")
        return data

    def fetch_changelog(self, key: str) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._read(f"{key}.changelog.json")
        return data

    def fetch_status_categories(self, project_key: str) -> dict[str, str]:
        return status_categories(self._read(f"{project_key}.statuses.json"))

    def group_members(self, group: str) -> set[str]:
        if not re.fullmatch(r"[\w.-]+", group):
            raise ValueError(f"{group!r} is not a group name")
        return set(self._read(f"groups/{group}.json"))


def _is_work_item_source(source: FixtureJiraSource) -> WorkItemSource:
    return source  # mypy proves FixtureJiraSource satisfies the protocol
