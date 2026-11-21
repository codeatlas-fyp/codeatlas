"""Frozen development evidence runs without a provider or mutable dataset."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import load_bundle
from codeatlas.cli import app
from codeatlas.config import DEFAULT_POLICY
from codeatlas.pipeline import evaluate_bundle
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
