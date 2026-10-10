"""CLI end to end on recorded Jira data (step-8 spec AC1-AC8, AC10, AC11). Network is blocked."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from codeatlas.bundle.hashing import sha256_hex
from codeatlas.bundle.store import BundleStore
from codeatlas.cli import EXIT_CODES, app

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "jira"
POLICY = ROOT / "config" / "policy.example.yaml"
runner = CliRunner()


def change_file(tmp_path: Path, keys: list[str], commits: list[str] | None = None) -> Path:
    data: dict[str, Any] = {
        "repo": "codeatlas-fyp/codeatlas-sandbox",
        "pr_number": 7,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "merge_base_sha": "c" * 40,
        "work_items": [{"key": k, "found_in": "pr_title", "commit_sha": None} for k in keys],
        "commits": [
            {
                "sha": f"{i:040d}",
                "author_email": "user-02@example.test",
                "author_login": "user-02",
                "committed_at": at,
                "message": f"{keys[0] if keys else 'x'}: work",
            }
            for i, at in enumerate(commits or ["2026-10-08T09:00:00Z"])
        ],
    }
    path = tmp_path / "change.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def collect(tmp_path: Path, keys: list[str], *extra: str) -> Any:
    store = tmp_path / "store"
    args = ["collect", "--change", str(change_file(tmp_path, keys)), "--policy", str(POLICY)]
    args += ["--fixtures", str(FIXTURES), "--store", str(store), *extra]
    return runner.invoke(app, args)


def stored_folders(tmp_path: Path) -> list[Path]:
    store = tmp_path / "store"
    return [p for p in store.iterdir() if p.is_dir()] if store.exists() else []


# --- AC1: collect writes a hash-true bundle and its verdict -------------------------------------


def test_ac1_collect_writes_bundle_and_verdict(tmp_path: Path) -> None:
    result = collect(tmp_path, ["SBX-6"])

    (folder,) = stored_folders(tmp_path)
    assert sha256_hex((folder / "bundle.json").read_bytes()) == folder.name
    assert (folder / "verdict.json").is_file()
    assert folder.name in result.stdout
    # SBX-6 was never approved: C3 fails, so the verdict is FAIL with exit code 1.
    assert "FAIL" in result.stdout
    assert result.exit_code == 1


# --- AC2 and AC4: offline evaluation, identical in two processes --------------------------------


def test_ac2_ac4_evaluate_twice_in_separate_processes(tmp_path: Path) -> None:
    collect(tmp_path, ["SBX-6"])
    (folder,) = stored_folders(tmp_path)
    code = "from codeatlas.cli import app; app()"
    hashes = []
    for _ in range(2):
        subprocess.run(
            [sys.executable, "-c", code, "evaluate", "--bundle", str(folder)],
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )
        hashes.append(json.loads((folder / "verdict.json").read_text())["verdict_hash"])

    assert hashes[0] == hashes[1]


# --- AC3: tampered bundle ----------------------------------------------------------------------


def test_ac3_tampered_bundle_replay_exits_5(tmp_path: Path) -> None:
    collect(tmp_path, ["SBX-6"])
    (folder,) = stored_folders(tmp_path)
    assert runner.invoke(app, ["replay", str(folder)]).exit_code == 0
    path = folder / "bundle.json"
    path.write_bytes(path.read_bytes().replace(b"SBX-6", b"SBX-5", 1))
    (folder / "verdict.json").unlink()

    result = runner.invoke(app, ["replay", str(folder)])

    assert result.exit_code == 5
    assert not (folder / "verdict.json").exists()
    assert runner.invoke(app, ["evaluate", "--bundle", str(folder)]).exit_code == 5


# --- AC5, AC6: Jira failures --------------------------------------------------------------------


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("CODEATLAS_JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("CODEATLAS_JIRA_EMAIL", "user-01@example.test")
    monkeypatch.setenv("CODEATLAS_JIRA_TOKEN", "very-secret-token")
    monkeypatch.setenv("CODEATLAS_JIRA_MAX_RETRIES", "0")


def live_collect(tmp_path: Path) -> Any:
    args = ["collect", "--change", str(change_file(tmp_path, ["SBX-6"])), "--policy", str(POLICY)]
    return runner.invoke(app, [*args, "--store", str(tmp_path / "store")])


@respx.mock
def test_ac5_jira_unreachable_exits_3_without_bundle_or_token(
    tmp_path: Path, live_env: None
) -> None:
    respx.route(host="example.atlassian.net").mock(side_effect=httpx.ConnectError("unreachable"))

    result = live_collect(tmp_path)

    assert result.exit_code == 3
    assert stored_folders(tmp_path) == []
    assert "SourceUnavailable" in result.stderr
    assert "very-secret-token" not in result.stderr + result.stdout


@respx.mock
def test_ac6_wrong_token_exits_3(tmp_path: Path, live_env: None) -> None:
    respx.route(host="example.atlassian.net").respond(401)

    result = live_collect(tmp_path)

    assert result.exit_code == 3
    assert stored_folders(tmp_path) == []
    assert "401" in result.stderr


def test_missing_jira_settings_exit_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("CODEATLAS_JIRA_BASE_URL", "CODEATLAS_JIRA_EMAIL", "CODEATLAS_JIRA_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    result = live_collect(tmp_path)

    assert result.exit_code == 4
    assert "CODEATLAS_JIRA_BASE_URL" in result.stderr


# --- AC7, AC8, AC11 --------------------------------------------------------------------------


def test_ac7_no_work_item_is_unknown_exit_2(tmp_path: Path) -> None:
    result = collect(tmp_path, [])

    assert result.exit_code == 2
    assert "UNKNOWN" in result.stdout
    assert "no work item" in result.stdout


def test_ac8_exit_codes() -> None:
    assert EXIT_CODES == {"PASS": 0, "FAIL": 1, "REVIEW": 2, "UNKNOWN": 2}


def test_ac8_passing_bundle_exits_0(tmp_path: Path) -> None:
    # A bundle built in code whose requirement was approved after its last edit, with no commits
    # after it and no priority change: every Phase 1 rule is satisfied or not applicable.
    from tests.unit.test_rules import approval, bundle, edit, facts

    passing = bundle(
        lifecycle=[facts(approvals=[approval(5)], edits=[3])], edits=[edit(3)], commits=[10]
    )
    hash_ = BundleStore(tmp_path).save(passing)

    result = runner.invoke(app, ["evaluate", "--bundle", str(tmp_path / hash_)])

    assert result.exit_code == 0, result.stdout
    assert "PASS" in result.stdout


def test_ac8_invalid_policy_exits_4(tmp_path: Path) -> None:
    bad = tmp_path / "policy.yaml"
    bad.write_text("version: [", encoding="utf-8")
    args = ["collect", "--change", str(change_file(tmp_path, ["SBX-6"])), "--policy", str(bad)]

    result = runner.invoke(app, [*args, "--fixtures", str(FIXTURES)])

    assert result.exit_code == 4


def test_ac11_missing_issue_is_a_note_not_a_crash(tmp_path: Path) -> None:
    result = collect(tmp_path, ["SBX-999"])

    (folder,) = stored_folders(tmp_path)
    bundle = json.loads((folder / "bundle.json").read_text())
    assert "work item SBX-999 not found" in bundle["collection_notes"]
    verdict = json.loads((folder / "verdict.json").read_text())
    c3 = next(r for r in verdict["results"] if r["case"] == "C3")
    assert c3["outcome"] == "INSUFFICIENT_EVIDENCE"
    assert result.exit_code == 2


# --- AC10: show -----------------------------------------------------------------------------


def test_ac10_show_prints_verdict_and_results(tmp_path: Path) -> None:
    collect(tmp_path, ["SBX-6"])
    (folder,) = stored_folders(tmp_path)

    text = runner.invoke(app, ["show", str(folder)])
    as_json = runner.invoke(app, ["show", str(folder), "--json"])

    assert text.exit_code == 0
    for case in ("C1", "C3", "C6", "C7", "C8"):
        assert case in text.stdout
    assert json.loads(as_json.stdout)["bundle_hash"] == folder.name


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip()


def test_sample_bundle_shows_and_replays() -> None:
    folder = next(p for p in (ROOT / "tests" / "fixtures" / "bundles").iterdir() if p.is_dir())

    assert runner.invoke(app, ["replay", str(folder)]).exit_code == 0
    shown = json.loads(runner.invoke(app, ["show", str(folder), "--json"]).stdout)
    assert shown["bundle_hash"] == folder.name
    assert shown["value"] == "FAIL"  # the sample's approval is stale (C3)


def test_default_policy_with_unreadable_groups_makes_approval_unknown(tmp_path: Path) -> None:
    # The built-in policy names approver groups; SBX has no such groups (404), and no account
    # ids are listed, so nobody is known to be an approver: C3 cannot judge.
    args = ["collect", "--change", str(change_file(tmp_path, ["SBX-6"])), "--fixtures"]
    result = runner.invoke(app, [*args, str(FIXTURES), "--store", str(tmp_path / "store")])

    (folder,) = stored_folders(tmp_path)
    notes = json.loads((folder / "bundle.json").read_text())["collection_notes"]
    assert "group sbx-requirement-approvers not readable (HTTP 404)" in notes
    assert "approvers unknown: SBX-6" in notes
    assert "no policy file: built-in default policy used" in notes
    verdict = json.loads((folder / "verdict.json").read_text())
    assert next(r for r in verdict["results"] if r["case"] == "C3")["outcome"] == (
        "INSUFFICIENT_EVIDENCE"
    )
    assert result.exit_code == 2


def test_group_lookup_server_error_is_not_swallowed() -> None:
    from codeatlas.errors import CollectionError
    from codeatlas.pipeline import _members

    class Broken:
        def group_members(self, group: str) -> set[str]:
            raise CollectionError("jira", status=500, retryable=True)

    with pytest.raises(CollectionError):
        _members(Broken(), "approvers", [], [])  # type: ignore[arg-type]
