"""Collector interfaces (spec §7.2). Collectors return raw JSON so it can be recorded."""

from typing import Any, Protocol


class WorkItemSource(Protocol):
    def fetch_issue(self, key: str) -> dict[str, Any]: ...  # raw Jira JSON

    def fetch_changelog(self, key: str) -> list[dict[str, Any]]: ...  # all pages, oldest first

    def group_members(self, group: str) -> set[str]: ...  # account IDs


class ChangeSource(Protocol):
    def pull_request(self, repo: str, number: int) -> dict[str, Any]: ...

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]: ...

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]: ...
