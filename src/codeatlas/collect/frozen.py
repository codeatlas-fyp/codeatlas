"""Strict, offline adapters for a checksummed evaluation-repository case.

Legacy cases require historical status-category evidence. Contract v4 stores cutoff
snapshots and treats unavailable lifecycle information conservatively.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pygit2
import yaml

from codeatlas.analyze.keys import find_keys
from codeatlas.collect.git.repo import LocalRepo
from codeatlas.collect.jira.client import status_categories
from codeatlas.config import load_identity_map, load_policy
from codeatlas.errors import CollectionError, ConfigError
from codeatlas.schema import IdentityMap, Policy

_KEY = re.compile(r"[A-Z][A-Z0-9_]*-\d+\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _time(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ConfigError("Frozen case timestamps must include a timezone.")
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise ConfigError("Frozen case has an invalid timestamp.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ConfigError("Frozen case has an invalid timestamp.") from error
    if result.tzinfo is None:
        raise ConfigError("Frozen case timestamps must include a timezone.")
    return result.astimezone(UTC)


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError("Frozen case contains missing or invalid JSON evidence.") from error


class FrozenJira:
    """Read only the issue, history, categories and memberships covered by a manifest."""

    def __init__(
        self, root: Path, listed: set[str], cutoff: datetime, *, contract_v4: bool = False
    ):
        self.root = root
        self.listed = listed
        self.cutoff = cutoff
        self.contract_v4 = contract_v4

    def _read(self, relative: str) -> Any:
        if relative not in self.listed:
            raise ConfigError(f"Frozen case lacks required evidence: {relative}")
        return _json(self.root / relative)

    def fetch_issue(self, key: str) -> dict[str, Any]:
        if not _KEY.fullmatch(key):
            raise ConfigError("Frozen case has an invalid Jira key.")
        issue = self._read(f"sources/jira_{key}.json")
        if not isinstance(issue, dict) or issue.get("key") != key:
            raise ConfigError("Frozen Jira issue does not match its key.")
        fields = issue.get("fields")
        if not isinstance(fields, dict):
            raise ConfigError("Frozen Jira issue fields are missing.")
        updated = fields.get("updated")
        if not isinstance(updated, str) or _time(updated) > self.cutoff:
            raise ConfigError("Frozen Jira issue cannot be verified at the decision cutoff.")
        return issue

    def fetch_changelog(self, key: str) -> list[dict[str, Any]]:
        issue = self.fetch_issue(key)
        changelog = issue.get("changelog")
        if not isinstance(changelog, dict):
            raise ConfigError("Frozen Jira changelog is missing.")
        history = changelog.get("histories")
        if not isinstance(history, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("created"), str)
            or _time(item["created"]) > self.cutoff
            for item in history
        ):
            raise ConfigError("Frozen Jira changelog is missing or extends beyond the cutoff.")
        return history

    def fetch_status_categories(self, project_key: str) -> dict[str, str]:
        data = self._read(f"sources/{project_key}.statuses.json")
        if not isinstance(data, list):
            raise ConfigError("Frozen Jira status categories are invalid.")
        categories = status_categories(data)
        if not categories:
            raise ConfigError("Frozen Jira status categories are empty.")
        return categories

    def fetch_cutoff_snapshot(self, key: str) -> dict[str, Any]:
        """Read v4 cutoff evidence, without consulting the as-fetched issue."""
        if not self.contract_v4 or not _KEY.fullmatch(key):
            raise ConfigError("Frozen cutoff evidence is unavailable.")
        relative = f"sources/jira_{key}_at_cutoff.txt"
        if relative not in self.listed:
            raise ConfigError(f"Frozen case lacks required evidence: {relative}")
        try:
            raw = (self.root / relative).read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError("Frozen cutoff text cannot be read.") from error
        header, separator, body = raw.partition("\n---\n")
        if not separator:
            raise ConfigError("Frozen cutoff text is invalid.")
        fields = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
        status = fields.get("reconstruction_status")
        if fields.get("key") != key or _time(fields.get("cutoff")) != self.cutoff:
            raise ConfigError("Frozen cutoff text does not match the case.")
        if status not in {"reconstructed", "created_text_only", "unverifiable", "inaccessible"}:
            raise ConfigError("Frozen cutoff text reconstruction status is invalid.")
        if status in {"unverifiable", "inaccessible"} and body.strip():
            raise ConfigError("Unavailable frozen cutoff text must be empty.")
        state = self._read(f"sources/jira_{key}_state_at_cutoff.json")
        if not isinstance(state, dict) or state.get("key") != key:
            raise ConfigError("Frozen Jira cutoff state does not match its key.")
        if _time(state.get("cutoff")) != self.cutoff:
            raise ConfigError("Frozen Jira cutoff state has the wrong cutoff.")
        states = state.get("fields")
        category = state.get("status_category")
        if not isinstance(states, dict) or not isinstance(category, dict):
            raise ConfigError("Frozen Jira cutoff state is incomplete.")
        if category.get("reconstruction") != "unavailable" or category.get("value") is not None:
            raise ConfigError("Frozen status category must be unavailable.")
        for name in ("status", "priority", "assignee", "resolution"):
            entry = states.get(name)
            if not isinstance(entry, dict) or entry.get("reconstruction") not in {
                "reconstructed", "unverifiable", "unavailable"
            }:
                raise ConfigError("Frozen Jira cutoff field status is invalid.")
            if (
                state.get("changelog_complete") is False
                and entry["reconstruction"] == "reconstructed"
            ):
                raise ConfigError("Frozen Jira cutoff state has an incomplete changelog.")
            if entry["reconstruction"] != "reconstructed" and (
                entry.get("value") is not None or entry.get("value_id") is not None
            ):
                raise ConfigError("Unavailable frozen Jira state must not contain a value.")
            changes = entry.get("changes_up_to_cutoff")
            if not isinstance(changes, list) or any(
                not isinstance(change, dict)
                or _time(change.get("at")) > self.cutoff
                for change in changes
            ):
                raise ConfigError("Frozen Jira transitions are invalid or after the cutoff.")
        return {
            "key": key,
            "cutoff": self.cutoff.isoformat(),
            "text": body.rstrip("\n") if status in {"reconstructed", "created_text_only"} else None,
            "text_status": status,
            "text_note": fields.get("reconstruction_note", ""),
            "state": state,
        }

    def group_members(self, group: str) -> set[str]:
        if not re.fullmatch(r"[\w.-]+", group):
            raise ConfigError("Frozen case has an invalid group name.")
        relative = f"sources/groups/{group}.json"
        if relative not in self.listed:
            raise CollectionError(
                "jira", status=403, retryable=False, detail="membership unavailable"
            )
        members = self._read(relative)
        if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
            raise ConfigError("Frozen group membership is invalid.")
        return set(members)


class FrozenGitHub:
    def __init__(
        self,
        repo: str,
        number: int,
        pr: dict[str, Any],
        commits: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
    ):
        self.repo, self.number, self.pr = repo, number, pr
        self._commits, self._reviews = commits, reviews

    def _check(self, repo: str, number: int) -> None:
        if (repo, number) != (self.repo, self.number):
            raise ConfigError("Frozen case does not match the requested pull request.")

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        self._check(repo, number)
        return deepcopy(self.pr)

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        self._check(repo, number)
        return deepcopy(self._commits)

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        self._check(repo, number)
        return deepcopy(self._reviews)


@dataclass(frozen=True)
class FrozenCase:
    repo: str
    number: int
    cutoff: datetime
    policy: Policy
    identity_map: IdentityMap
    policy_source: str
    identity_map_source: str
    jira: FrozenJira
    github: FrozenGitHub
    git: LocalRepo


def load_frozen_case(case_dir: Path, repo_path: Path) -> FrozenCase:
    """Verify a manifest and its historical sources; never contact a network service."""
    root = case_dir.resolve()
    manifest = _json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("freeze_status") != "complete":
        raise ConfigError("Frozen case manifest is incomplete.")
    files = manifest.get("files")
    mandatory = manifest.get("mandatory_files")
    refs = manifest.get("refs")
    if not isinstance(files, dict) or not isinstance(mandatory, list) or not isinstance(refs, dict):
        raise ConfigError("Frozen case manifest is invalid.")
    required = {
        "case.yaml",
        "sources/pr.json",
        "sources/commits.json",
        "sources/reviews.json",
        "sources/files.json",
        "sources/history.json",
        "sources/jira_links.json",
        "sources/CODEOWNERS_at_base.txt",
        "sources/effective_policy.yaml",
    }
    if not required.issubset(files) or any(
        not isinstance(item, str) or item not in files for item in mandatory
    ):
        raise ConfigError("Frozen case is missing mandatory checksummed evidence.")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ConfigError("Frozen case manifest contains an invalid file entry.")
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ConfigError("Frozen case contains an invalid evidence path.")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ConfigError("Frozen case evidence cannot be read.") from error
        if digest != expected:
            raise ConfigError(f"Frozen case checksum mismatch: {relative}")
    try:
        metadata = yaml.safe_load((root / "case.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError("Frozen case metadata is invalid.") from error
    if not isinstance(metadata, dict):
        raise ConfigError("Frozen case metadata is invalid.")
    repo, number = metadata.get("repo"), metadata.get("pr_number")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or not isinstance(number, int):
        raise ConfigError("Frozen case repository or pull request is invalid.")
    cutoff = _time(metadata.get("decision_cutoff_utc"))
    contract_v4 = manifest.get("freeze_tool_version") == "freeze_case.py 4"
    if not contract_v4 and metadata.get("status_categories_at_cutoff") is not True:
        raise ConfigError("Frozen case lacks verified historical status-category evidence.")
    if (
        any(path.startswith("sources/groups/") for path in files)
        and metadata.get("group_membership_at_cutoff") is not True
    ):
        raise ConfigError("Frozen group membership is not verified at the cutoff.")
    history = _json(root / "sources/history.json")
    if not isinstance(history, dict) or history.get("historical_diff_status") != "verified":
        raise ConfigError("Frozen case historical diff is not verified.")
    if history.get("historical_content_status") != "identical":
        raise ConfigError("Frozen case historical content is not verified identical.")
    for name in ("base_at_merge", "head_at_merge", "merge_base_sha"):
        if (
            not isinstance(history.get(name), str)
            or metadata.get(name) != history[name]
            or refs.get(name) != history[name]
        ):
            raise ConfigError("Frozen case historical revisions are inconsistent.")
    if refs.get("policy_sha256") != files["sources/effective_policy.yaml"]:
        raise ConfigError("Frozen case policy hash does not match the manifest.")
    policy, _ = load_policy(root / "sources/effective_policy.yaml")
    identity_path = "sources/identity_map.yaml"
    identity = (
        load_identity_map(root / identity_path)
        if identity_path in files
        else IdentityMap(people=[])
    )
    try:
        git = LocalRepo(repo_path)
    except (OSError, ValueError, pygit2.GitError) as error:
        raise ConfigError("Frozen case local repository is unavailable.") from error
    try:
        if (
            git.merge_base(history["base_at_merge"], history["head_at_merge"])
            != history["merge_base_sha"]
        ):
            raise ConfigError("Frozen case merge base does not match the local repository.")
        deltas = git.diff_files(history["merge_base_sha"], history["head_at_merge"])
    except (KeyError, ValueError, pygit2.GitError) as error:
        raise ConfigError(
            "Frozen case revisions are unavailable in the local repository."
        ) from error
    file_records = _json(root / "sources/files.json")
    if not isinstance(file_records, list) or any(
        not isinstance(f, dict) or not isinstance(f.get("filename"), str) for f in file_records
    ):
        raise ConfigError("Frozen case changed-file list is invalid.")
    if {d.new_path or d.old_path for d in deltas} != {f["filename"] for f in file_records}:
        raise ConfigError("Frozen case changed files do not match the local repository.")
    recorded_owner = (root / "sources/CODEOWNERS_at_base.txt").read_bytes()
    actual_owner = next(
        (
            content
            for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
            if (content := git.file_bytes(history["base_at_merge"], path)) is not None
        ),
        None,
    )
    if recorded_owner != (
        actual_owner if actual_owner is not None else b"NO CODEOWNERS FILE AT BASE\n"
    ):
        raise ConfigError("Frozen CODEOWNERS evidence does not match the base revision.")
    pr = _json(root / "sources/pr.json")
    commits, reviews = _json(root / "sources/commits.json"), _json(root / "sources/reviews.json")
    links = _json(root / "sources/jira_links.json")
    if (
        not isinstance(pr, dict)
        or not isinstance(commits, list)
        or not isinstance(reviews, list)
        or not isinstance(links, dict)
        or not isinstance(links.get("links"), list)
    ):
        raise ConfigError("Frozen GitHub or link evidence is invalid.")
    if any(
        not isinstance(item, dict) or item.get("status") != "verified" for item in links["links"]
    ):
        raise ConfigError("Frozen case has uncertain requirement links.")
    if any(
        not isinstance(review, dict)
        or not isinstance(review.get("submitted_at"), str)
        or _time(review["submitted_at"]) > cutoff
        for review in reviews
    ):
        raise ConfigError("Frozen review evidence is invalid or after the cutoff.")
    for review in reviews:
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            if (
                not isinstance(review.get("commit_id"), str)
                or not isinstance(review.get("user"), dict)
                or not isinstance(review["user"].get("login"), str)
            ):
                raise ConfigError("Frozen decision review is missing its actor or commit.")
    if not isinstance(pr.get("base"), dict) or not isinstance(pr.get("head"), dict):
        raise ConfigError("Frozen pull request metadata is incomplete.")
    recorded_commits = [commit.get("sha") for commit in commits if isinstance(commit, dict)]
    try:
        walker = git._repo.walk(pygit2.Oid(hex=history["head_at_merge"]))
        walker.hide(pygit2.Oid(hex=history["base_at_merge"]))
        expected_commits = {str(commit.id) for commit in walker}
    except (KeyError, ValueError, pygit2.GitError) as error:
        raise ConfigError("Frozen commit history is unavailable locally.") from error
    if (
        len(recorded_commits) != len(commits)
        or not all(isinstance(sha, str) for sha in recorded_commits)
        or len(set(recorded_commits)) != len(recorded_commits)
        or set(recorded_commits) != expected_commits
        or not expected_commits
    ):
        raise ConfigError("Frozen commit evidence does not match the historical Git history.")
    for commit in commits:
        if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
            raise ConfigError("Frozen commit evidence is invalid.")
        details = commit.get("commit")
        if not isinstance(details, dict) or not isinstance(details.get("committer"), dict):
            raise ConfigError("Frozen commit metadata is invalid.")
        date = details["committer"].get("date")
        if not isinstance(date, str) or _time(date) > cutoff:
            raise ConfigError("Frozen commit evidence is invalid or after the cutoff.")
        try:
            head = pygit2.Oid(hex=history["head_at_merge"])
            oid = pygit2.Oid(hex=commit["sha"])
            if oid != head and not git._repo.descendant_of(head, oid):
                raise ConfigError("Frozen commit is not an ancestor of the historical head.")
            actual = git._repo[oid]
            if (
                not isinstance(actual, pygit2.Commit)
                or details.get("message") != actual.message
                or _time(date) != datetime.fromtimestamp(actual.commit_time, UTC)
            ):
                raise ConfigError("Frozen commit metadata does not match the local Git object.")
        except (ValueError, KeyError, pygit2.GitError) as error:
            raise ConfigError("Frozen commit is unavailable in the local repository.") from error
    pr["base"]["sha"], pr["head"]["sha"] = history["base_at_merge"], history["head_at_merge"]
    pr["title"], pr["body"] = links.get("title_at_merge") or "", ""
    found = find_keys(
        branch=pr["head"].get("ref"),
        pr_title=pr["title"],
        pr_body="",
        commits=commits,
        pattern=policy.workitem_key_pattern,
        retrieved_at=cutoff,
    )
    if {item.key for item in found} != {item.get("key") for item in links["links"]}:
        raise ConfigError("Frozen requirement links cannot be reproduced from historical sources.")
    jira = FrozenJira(root, set(files), cutoff, contract_v4=contract_v4)
    for key in {item.key for item in found}:
        if contract_v4:
            jira.fetch_cutoff_snapshot(key)
        else:
            jira.fetch_issue(key)
            jira.fetch_changelog(key)
            jira.fetch_status_categories(key.split("-")[0])
    digest = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    return FrozenCase(
        repo,
        number,
        cutoff,
        policy,
        identity,
        f"frozen manifest sha256:{digest}",
        f"frozen manifest sha256:{digest}"
        if identity_path in files
        else "identity map not provided",
        jira,
        FrozenGitHub(repo, number, pr, commits, reviews),
        git,
    )
