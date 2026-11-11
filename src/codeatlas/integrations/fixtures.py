"""Explicit local fixture adapters for end-to-end development without external credentials."""

import json
import shutil
from pathlib import Path
from typing import Any

from codeatlas.api.settings import WebSettings
from codeatlas.collect.git.repo import LocalRepo
from codeatlas.collect.github.recorder import FixtureSource
from codeatlas.collect.jira.recorder import FixtureJiraSource

FIXTURE_REPO = "codeatlas-fyp/codeatlas-sandbox"


class MappedFixtureGitHub:
    def __init__(self, root: Path, mapping: dict[str, str]):
        self.source = FixtureSource(root)
        self.mapping = mapping

    def _map(self, data: Any) -> Any:
        if isinstance(data, str):
            for old, new in self.mapping.items():
                data = data.replace(old, new)
            return data
        if isinstance(data, list):
            return [self._map(item) for item in data]
        if isinstance(data, dict):
            return {key: self._map(value) for key, value in data.items()}
        return data

    def pull_request(self, repo: str, number: int) -> dict[str, Any]:
        return self._map(self.source.pull_request(repo, number))

    def commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._map(self.source.commits(repo, number))

    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return self._map(self.source.reviews(repo, number))


def fixture_pr(settings: WebSettings, number: int) -> dict[str, Any]:
    path = (
        settings.github_fixture_dir / FIXTURE_REPO.replace("/", "__") / f"pr-{number}" / "pr.json"
    )
    if not path.is_file():
        raise FileNotFoundError("no recorded pull request with that number")
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_pull_requests(settings: WebSettings) -> list[dict[str, Any]]:
    repository_dir = settings.github_fixture_dir / FIXTURE_REPO.replace("/", "__")
    found = []
    for directory in sorted(repository_dir.glob("pr-*")):
        try:
            found.append(fixture_pr(settings, int(directory.name.removeprefix("pr-"))))
        except (ValueError, OSError):
            continue
    return found


def fixture_repository(settings: WebSettings, number: int) -> tuple[LocalRepo, dict[str, str]]:
    """Construct deterministic local Git history corresponding to the committed recordings."""
    import pygit2

    if number not in (1, 2):
        raise FileNotFoundError("fixture Git history is available for PRs 1 and 2")
    fixture_pr(settings, number)
    path = settings.repo_cache_dir / "fixtures" / "sandbox.git"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            repo = pygit2.Repository(str(path))
            base = str(repo.references["refs/codeatlas/base-1"].target)
            head1 = str(repo.references["refs/codeatlas/pr-1"].target)
            head2 = str(repo.references["refs/codeatlas/pr-2"].target)
            return LocalRepo(path), _mapping(base, head1, head2)
        except (KeyError, ValueError, pygit2.GitError):
            shutil.rmtree(path, ignore_errors=True)
    repo = pygit2.init_repository(str(path), bare=True)
    mapping = _build_fixture_commits(repo, settings)
    return LocalRepo(path), mapping


def _build_fixture_commits(repo, settings: WebSettings) -> dict[str, str]:
    github_root = settings.github_fixture_dir / FIXTURE_REPO.replace("/", "__")
    mini = Path(__file__).parents[3] / "tests" / "fixtures" / "repos" / "mini_sandbox"
    fees = (mini / "payments" / "fees.py").read_text(encoding="utf-8")
    base_files = {
        "CODEOWNERS": (github_root / "pr-1" / "codeowners.txt").read_text(encoding="utf-8"),
        "loans.py": (mini / "loans.py").read_text(encoding="utf-8"),
        "payments/__init__.py": "",
        "payments/fees.py": fees,
        "docs/adr/0001-fees.md": (
            "---\nstatus: accepted\nscope: [payments/**]\n"
            "requirement_keys: [SBX-4]\n---\n# Fees live in payments\n"
        ),
    }
    base = _commit(repo, base_files, [], "init")
    head1 = _commit(
        repo,
        {
            **base_files,
            "payments/fees.py": fees + "\n\ndef round_fee_to_cents(fee):\n    return fee\n",
        },
        [base],
        "feat(payments): add fee rounding (SBX-4)",
    )
    head2 = _commit(
        repo,
        {
            **base_files,
            "CODEOWNERS": base_files["CODEOWNERS"] + "/loans.py @user-02\n",
            "loans.py": base_files["loans.py"]
            + "\n\ndef count_open_loans(loans):\n    return len(loans)\n",
        },
        [base],
        "chore(ownership): transfer loans ownership (SBX-5)",
    )
    values = (str(base), str(head1), str(head2))
    for number, head in ((1, head1), (2, head2)):
        repo.references.create(f"refs/codeatlas/base-{number}", base, force=True)
        repo.references.create(f"refs/codeatlas/pr-{number}", head, force=True)
    return _mapping(*values)


def _mapping(base: str, head1: str, head2: str) -> dict[str, str]:
    return {
        "basesha0000000000000000000000000000000001": base,
        "headsha0000000000000000000000000000000001": head1,
        "basesha0000000000000000000000000000000002": base,
        "headsha0000000000000000000000000000000002": head2,
    }


def _commit(repo, files: dict[str, str], parents: list, message: str):
    import pygit2

    index = pygit2.Index()
    for name, text in sorted(files.items()):
        blob = repo.create_blob(text.encode("utf-8"))
        index.add(pygit2.IndexEntry(name, blob, pygit2.GIT_FILEMODE_BLOB))
    tree = index.write_tree(repo)
    signature = pygit2.Signature("Fixture User", "fixture@example.test", 1_760_000_000, 0)
    return repo.create_commit(None, signature, signature, message, tree, parents)


def fixture_sources(settings: WebSettings, number: int):
    from codeatlas.pipeline import ChangeSources

    git, mapping = fixture_repository(settings, number)
    github = MappedFixtureGitHub(settings.github_fixture_dir, mapping)
    jira = FixtureJiraSource(settings.jira_fixture_dir)
    return jira, ChangeSources(github=github, git=git)
