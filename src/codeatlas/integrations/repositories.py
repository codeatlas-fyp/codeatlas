"""Managed bare Git snapshots fetched through libgit2 with exact commit verification."""

import os
import re
import shutil
import time
from contextlib import suppress
from pathlib import Path

from filelock import FileLock, Timeout

from codeatlas.api.settings import WebSettings
from codeatlas.collect.git.repo import LocalRepo
from codeatlas.integrations.connections import ConnectionValidationError, validate_repo

_BRANCH = re.compile(r"[A-Za-z0-9._/-]+\Z")


class RepositoryPreparationError(RuntimeError):
    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class RepositoryManager:
    def __init__(self, settings: WebSettings):
        self.settings = settings
        self.root = settings.repo_cache_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def prepare(
        self,
        repository: str,
        pr_number: int,
        base_ref: str,
        base_sha: str,
        head_sha: str,
        token: str,
    ) -> LocalRepo:
        try:
            repo_name = validate_repo(repository)
        except ConnectionValidationError as error:
            raise RepositoryPreparationError("invalid_repository", error.message) from error
        if pr_number <= 0 or not _valid_branch(base_ref):
            raise RepositoryPreparationError(
                "invalid_ref", "The pull request contains an invalid Git reference."
            )
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_sha) or not re.fullmatch(
            r"[0-9a-fA-F]{40,64}", head_sha
        ):
            raise RepositoryPreparationError(
                "invalid_sha", "GitHub returned an invalid commit identifier."
            )

        # Hashing validated owner/name gives a fixed path without accepting a filesystem path.
        import hashlib

        key = hashlib.sha256(repo_name.lower().encode("utf-8")).hexdigest()
        repo_path = (self.root / key).resolve()
        if self.root not in repo_path.parents:
            raise RepositoryPreparationError(
                "invalid_repository", "Repository cache path is invalid."
            )
        lock = FileLock(
            str(self.root / f"{key}.lock"), timeout=self.settings.repo_fetch_timeout_seconds
        )
        try:
            with lock:
                repo = self._open_or_initialize(repo_path)
                self._fetch(repo, repo_name, pr_number, base_ref, token)
                self._check_size(repo_path)
                refs = repo.references
                try:
                    actual_base = str(refs[f"refs/codeatlas/base-{pr_number}"].target)
                    actual_head = str(refs[f"refs/codeatlas/pr-{pr_number}"].target)
                except KeyError as error:
                    raise RepositoryPreparationError(
                        "missing_ref",
                        "GitHub did not provide the required base and pull request refs.",
                    ) from error
                if (
                    actual_base.lower() != base_sha.lower()
                    or actual_head.lower() != head_sha.lower()
                ):
                    raise RepositoryPreparationError(
                        "sha_mismatch",
                        "The pull request changed while it was being prepared. "
                        "Refresh the PR list and retry.",
                    )
                # Confirm both exact commits resolve before handing the snapshot to the pipeline.
                repo.revparse_single(base_sha)
                repo.revparse_single(head_sha)
                os.utime(repo_path, None)
                self._cleanup(repo_path)
                return LocalRepo(repo_path)
        except RepositoryPreparationError:
            raise
        except Timeout as error:
            raise RepositoryPreparationError(
                "repo_timeout", "Repository fetch timed out."
            ) from error
        except Exception as error:
            raise RepositoryPreparationError(
                "repo_fetch", "The repository could not be prepared. Check access and retry."
            ) from error

    def _open_or_initialize(self, path: Path):
        import pygit2

        if path.exists():
            try:
                return pygit2.Repository(str(path))
            except (KeyError, ValueError, pygit2.GitError):
                self._remove_managed(path)
        path.mkdir(parents=True, exist_ok=True)
        return pygit2.init_repository(str(path), bare=True)

    def _fetch(self, repo, repository: str, pr_number: int, base_ref: str, token: str) -> None:
        import pygit2

        url = f"https://github.com/{repository}.git"
        try:
            remote = repo.remotes["origin"]
            remote.url = url
        except (KeyError, IndexError):
            remote = repo.remotes.create("origin", url)
        deadline = time.monotonic() + self.settings.repo_fetch_timeout_seconds

        class Callbacks(pygit2.RemoteCallbacks):
            def __init__(self) -> None:
                super().__init__()

            def credentials(self, _url, username_from_url, _allowed_types):
                return pygit2.UserPass(username_from_url or "x-access-token", token)

            def transfer_progress(self, stats) -> None:
                if time.monotonic() > deadline:
                    raise TimeoutError("fetch deadline exceeded")
                received = getattr(stats, "received_bytes", 0)
                if received and received > self_outer.settings.repo_max_bytes:
                    raise RepositoryPreparationError(
                        "repo_too_large", "Repository exceeds the configured fetch size limit."
                    )

        self_outer = self
        base_refspec = f"+refs/heads/{base_ref}:refs/codeatlas/base-{pr_number}"
        head_refspec = f"+refs/pull/{pr_number}/head:refs/codeatlas/pr-{pr_number}"
        remote.fetch([base_refspec, head_refspec], callbacks=Callbacks())

    def _check_size(self, path: Path) -> None:
        total = 0
        for file in path.rglob("*"):
            if file.is_file():
                total += file.stat().st_size
                if total > self.settings.repo_max_bytes:
                    self._remove_managed(path)
                    raise RepositoryPreparationError(
                        "repo_too_large", "Repository exceeds the configured cache size limit."
                    )

    def _remove_managed(self, path: Path) -> None:
        resolved = path.resolve()
        if self.root not in resolved.parents:
            raise RepositoryPreparationError(
                "invalid_cache_path", "Refusing to remove an unmanaged path."
            )
        shutil.rmtree(resolved, ignore_errors=True)

    def _cleanup(self, keep: Path) -> None:
        limit = max(1, self.settings.repo_cache_max_repositories)
        candidates = sorted(
            (
                item
                for item in self.root.iterdir()
                if item.is_dir() and re.fullmatch(r"[0-9a-f]{64}", item.name)
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in candidates[limit:]:
            if old == keep:
                continue
            lock = FileLock(str(self.root / f"{old.name}.lock"), timeout=0)
            with suppress(Timeout):
                with lock:
                    self._remove_managed(old)


def _valid_branch(value: str) -> bool:
    return bool(
        _BRANCH.fullmatch(value)
        and not value.startswith("/")
        and not value.endswith("/")
        and ".." not in value
        and "@{" not in value
        and not value.endswith(".lock")
    )
