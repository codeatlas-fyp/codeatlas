"""Tests for docs/specs/step-1-repo-hygiene.md (AC1-AC4). Each test is named after its AC."""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_commit_msgs.py"


def load_script() -> ModuleType:
    # scripts/ is not a package, so load the file directly.
    spec = importlib.util.spec_from_file_location("check_commit_msgs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_script()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def commit(repo: Path, subject: str) -> str:
    git(repo, "commit", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "--short", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "develop")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    commit(tmp_path, "chore: initial commit")
    return tmp_path


# --- AC1-AC2: commit subjects ------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "feat(schema): add evidence models",
        "fix: handle empty changelog",
        "docs(spec): add step 1 spec (#3)",
        "chore(ci): run pre-commit in CI",
        "test(bundle): cover tampered replay",
        "refactor(lifecycle): split chain check",
    ],
)
def test_ac1_conventional_subjects_pass(subject: str) -> None:
    assert checker.check_subject("abc1234", subject) is None


@pytest.mark.parametrize(
    "subject",
    [
        "CA-4: add parser",
        "Add parser",
        "feature: add parser",
        "feat:add parser",
        "feat(Schema): x",
        "feat: ",
    ],
)
def test_ac2_non_conventional_subjects_fail(subject: str) -> None:
    error = checker.check_subject("abc1234", subject)

    assert error is not None
    assert "abc1234" in error


# --- AC3: branch names -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "branch", ["feature/repo-hygiene", "fix/ci-cache", "docs/sandbox-guide", "develop"]
)
def test_ac3_allowed_branches_pass(branch: str) -> None:
    assert checker.check_branch(branch) is None


@pytest.mark.parametrize(
    "branch",
    ["feature/CA-4-parser", "Feature/x", "feature/Repo_Hygiene", "hotfix/x", "main", "feature/"],
)
def test_ac3_disallowed_branches_fail(branch: str) -> None:
    error = checker.check_branch(branch)

    assert error is not None
    assert branch in error


# --- AC4: whole range, as CI runs it -----------------------------------------------------------


def test_ac4_mixed_range_reports_only_bad_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    good = commit(repo, "feat(schema): add models")
    bad = commit(repo, "CA-5: add client")
    monkeypatch.chdir(repo)

    code = checker.main([base, "HEAD", "feature/schema-v0"])

    errors = capsys.readouterr().out.splitlines()
    assert code == 1
    assert len(errors) == 1
    assert errors[0].startswith("::error::")
    assert bad in errors[0]
    assert good not in errors[0]


def test_ac4_clean_range_exits_zero(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "feat(schema): add models")
    commit(repo, "test(schema): round-trip every model")
    monkeypatch.chdir(repo)

    assert checker.main([base, "HEAD", "feature/schema-v0"]) == 0
    assert capsys.readouterr().out == ""


def test_ac4_merge_commits_ignored(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "switch", "-q", "-c", "feature/x")
    commit(repo, "feat: add x")
    git(repo, "switch", "-q", "develop")
    commit(repo, "fix: tidy y")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch feature/x", "feature/x")
    monkeypatch.chdir(repo)

    assert checker.main([base, "HEAD"]) == 0


def test_main_without_branch_skips_branch_check(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "docs: explain setup")
    monkeypatch.chdir(repo)

    assert checker.main([base, "HEAD"]) == 0
    assert capsys.readouterr().out == ""
