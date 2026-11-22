"""Frozen development evidence runs without a provider or mutable dataset."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from codeatlas.api.app import create_app
from codeatlas.api.settings import WebSettings
from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import load_bundle, load_verdict
from codeatlas.cli import app
from codeatlas.config import DEFAULT_POLICY
from codeatlas.evaluate.rules.c7_test_evidence import FrozenTestEvidenceStub
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import CasePolicy
from tests.integration.test_pipeline_pr import AT, JIRA, REPO, _sources
from tests.integration.test_pipeline_pr import sandbox as sandbox_fixture  # noqa: F401


@pytest.fixture
def frozen_case(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, Path]:
    sandbox_data = request.getfixturevalue("sandbox_fixture")
    root = tmp_path / "case"
    sources = root / "sources"
    sources.mkdir(parents=True)
    adapters = _sources(sandbox_data)
    pr = adapters.github.pull_request(REPO, 1)
    commits = adapters.github.commits(REPO, 1)
    for commit in commits:
        git_commit = adapters.git._repo[commit["sha"]]
        commit["commit"]["committer"]["date"] = datetime.fromtimestamp(
            git_commit.commit_time, UTC
        ).isoformat()
    reviews = adapters.github.reviews(REPO, 1)
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    merge_base = adapters.git.merge_base(base, head)
    refs = {"base_at_merge": base, "head_at_merge": head, "merge_base_sha": merge_base}
    history = refs | {
        "historical_diff_status": "verified",
        "historical_content_status": "identical",
    }
    issue = json.loads((JIRA / "SBX-4.issue.json").read_text(encoding="utf-8"))
    issue["changelog"] = {
        "histories": json.loads((JIRA / "SBX-4.changelog.json").read_text(encoding="utf-8"))
    }
    objects = {
        "pr.json": pr,
        "commits.json": commits,
        "reviews.json": reviews,
        "files.json": [
            {"filename": d.new_path or d.old_path}
            for d in adapters.git.diff_files(merge_base, head)
        ],
        "history.json": history,
        "jira_links.json": {
            "title_at_merge": pr["title"],
            "links": [{"key": "SBX-4", "status": "verified"}],
        },
        "jira_SBX-4.json": issue,
        "SBX.statuses.json": json.loads((JIRA / "SBX.statuses.json").read_text(encoding="utf-8")),
    }
    for name, value in objects.items():
        (sources / name).write_text(json.dumps(value), encoding="utf-8")
    (sources / "CODEOWNERS_at_base.txt").write_bytes(adapters.git.file_bytes(base, "CODEOWNERS"))
    (sources / "effective_policy.yaml").write_text(
        yaml.safe_dump(DEFAULT_POLICY.model_dump(mode="json")), encoding="utf-8"
    )
    metadata = refs | {
        "repo": REPO,
        "pr_number": 1,
        "decision_cutoff_utc": AT.isoformat(),
        "status_categories_at_cutoff": True,
    }
    (root / "case.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    files = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }
    manifest = {
        "freeze_status": "complete",
        "files": files,
        "mandatory_files": sorted(files),
        "refs": refs | {"policy_sha256": files["sources/effective_policy.yaml"]},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, Path(sandbox_data["path"])


def test_frozen_case_executes_and_replays_offline(frozen_case, tmp_path: Path) -> None:
    case, repo = frozen_case
    store = tmp_path / "bundles"
    result = CliRunner().invoke(
        app,
        ["collect-frozen", "--case", str(case), "--repo-path", str(repo), "--store", str(store)],
    )
    assert result.exit_code in {0, 1, 2}, result.output
    bundles = [path for path in store.iterdir() if path.is_dir()]
    assert len(bundles) == 1
    bundle = load_bundle(bundles[0])
    assert bundle.collected_at == AT
    assert bundle.semantic_run is not None
    assert bundle.changed_files
    assert "calls" in bundle.extractor_versions
    assert replay(bundles[0], evaluate_bundle).match


def test_frozen_case_rejects_tampering(frozen_case, tmp_path: Path) -> None:
    case, repo = frozen_case
    (case / "sources/pr.json").write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "collect-frozen",
            "--case",
            str(case),
            "--repo-path",
            str(repo),
            "--store",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 4
    assert "checksum mismatch" in result.output


@pytest.mark.parametrize(
    "missing", ["status_attestation", "status_snapshot", "head_commit", "commit_message"]
)
def test_frozen_case_rejects_incomplete_historical_evidence(
    frozen_case, tmp_path: Path, missing: str
) -> None:
    case, repo = frozen_case
    if missing == "status_attestation":
        path = case / "case.yaml"
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
        del metadata["status_categories_at_cutoff"]
        path.write_text(yaml.safe_dump(metadata), encoding="utf-8")
    elif missing == "status_snapshot":
        path = case / "sources/SBX.statuses.json"
        path.write_text("[]", encoding="utf-8")
    elif missing == "head_commit":
        path = case / "sources/commits.json"
        path.write_text("[]", encoding="utf-8")
    else:
        path = case / "sources/commits.json"
        commits = json.loads(path.read_text(encoding="utf-8"))
        commits[0]["commit"]["message"] = "SBX-999: invented message"
        path.write_text(json.dumps(commits), encoding="utf-8")
    manifest_path = case / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][path.relative_to(case).as_posix()] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "collect-frozen", "--case", str(case), "--repo-path", str(repo),
            "--store", str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 4


def _make_v4(case: Path, *, available: bool) -> None:
    metadata_path = case / "case.yaml"
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("status_categories_at_cutoff")
    metadata_path.write_text(yaml.safe_dump(metadata), encoding="utf-8")
    source = case / "sources"
    (source / "jira_SBX-4_at_cutoff.txt").write_text(
        f"key: SBX-4\nsource: test\ncutoff: {AT.isoformat()}\n"
        f"reconstruction_status: {'reconstructed' if available else 'created_text_only'}\n"
        "reconstruction_note: verified fixture\n---\n"
        "Historical title\n\nHistorical description\n",
        encoding="utf-8",
    )
    field_status = "reconstructed" if available else "unavailable"
    states = {}
    for field in ("status", "priority", "assignee", "resolution"):
        states[field] = {
            "reconstruction": field_status,
            "value": ("In Progress" if field == "status" else None) if available else None,
            "value_id": ("3" if available and field == "status" else None),
            "note": "reconstructed fixture" if available else "no historical field evidence",
            "changes_up_to_cutoff": ([{
                "at": "2026-10-01T00:00:00Z", "author": "historical-user",
                "from": "Open", "to": "In Progress",
            }] if available and field == "status" else []),
        }
    state = {
        "key": "SBX-4", "source": "test", "cutoff": AT.isoformat(),
        "changelog_complete": available, "fields": states,
        "status_category": {"reconstruction": "unavailable", "value": None, "note": "unknown"},
    }
    (source / "jira_SBX-4_state_at_cutoff.json").write_text(json.dumps(state), encoding="utf-8")
    # The as-fetched file is deliberately incompatible with the historical state.
    (source / "jira_SBX-4.json").write_text(
        json.dumps({"key": "SBX-4", "fields": {
            "updated": "2099-01-01T00:00:00Z", "summary": "CURRENT MUST NOT BE USED"
        }}), encoding="utf-8"
    )
    manifest_path = case / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["freeze_tool_version"] = "freeze_case.py 4"
    manifest["files"] = {
        p.relative_to(case).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in case.rglob("*") if p.is_file() and p != manifest_path
    }
    manifest["mandatory_files"] = sorted(manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("available", [True, False], ids=["arrow-spark", "airflow"])
def test_v4_cutoff_snapshots_are_offline_and_replayable(frozen_case, tmp_path, available):
    case, repo = frozen_case
    _make_v4(case, available=available)
    store = tmp_path / "v4-store"
    result = CliRunner().invoke(
        app,
        ["collect-frozen", "--case", str(case), "--repo-path", str(repo), "--store", str(store)],
    )
    assert result.exit_code in {0, 1, 2}, result.output
    folder = next(path for path in store.iterdir() if path.is_dir())
    bundle = load_bundle(folder)
    snapshot = bundle.frozen_jira_cutoff["SBX-4"]
    assert snapshot["text"] == "Historical title\n\nHistorical description"
    assert snapshot["text_status"] == ("reconstructed" if available else "created_text_only")
    assert snapshot["state"]["fields"]["status"]["reconstruction"] == (
        "reconstructed" if available else "unavailable"
    )
    assert snapshot["state"]["status_category"]["value"] is None
    if available:
        assert snapshot["state"]["fields"]["status"]["changes_up_to_cutoff"][0]["to"] == (
            "In Progress"
        )
    assert not bundle.criteria
    assert any("semantic ranking unavailable" in note for note in bundle.collection_notes)
    assert "CURRENT MUST NOT BE USED" not in (folder / "bundle.json").read_text(encoding="utf-8")
    verdict = load_verdict(folder)
    checks = {check.case: check for check in verdict.results}
    assert all(
        checks[name].outcome == "INSUFFICIENT_EVIDENCE" for name in ("C1", "C3", "C4", "C6", "C8")
    )
    policy = bundle.policy.model_copy(
        update={"cases": {"C7": CasePolicy(severity="review", required_when_label="tests")}}
    )
    assert FrozenTestEvidenceStub().check(bundle, [], policy).outcome == "INSUFFICIENT_EVIDENCE"
    assert replay(folder, evaluate_bundle).match
    application = create_app(
        WebSettings(bundle_dir=store, database_url=f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    )
    with TestClient(application) as client:
        response = client.get(f"/api/cases/{folder.name}/requirements/SBX-4")
        assert response.status_code == 200
        assert response.json()["frozen_cutoff"]["text"] == snapshot["text"]
        assert client.get(f"/api/cases/{folder.name}/replay").json()["match"] is True


@pytest.mark.parametrize(
    "problem", ["future_transition", "inferred_category", "incomplete_history"]
)
def test_v4_rejects_inconsistent_historical_evidence(frozen_case, tmp_path, problem):
    case, repo = frozen_case
    _make_v4(case, available=True)
    path = case / "sources/jira_SBX-4_state_at_cutoff.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    if problem == "future_transition":
        state["fields"]["status"]["changes_up_to_cutoff"][0]["at"] = "2099-01-01T00:00:00Z"
    elif problem == "inferred_category":
        state["status_category"] = {"reconstruction": "reconstructed", "value": "new"}
    else:
        state["changelog_complete"] = False
    path.write_text(json.dumps(state), encoding="utf-8")
    manifest_path = case / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][path.relative_to(case).as_posix()] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = CliRunner().invoke(
        app,
        ["collect-frozen", "--case", str(case), "--repo-path", str(repo),
         "--store", str(tmp_path / "out")],
    )
    assert result.exit_code == 4
