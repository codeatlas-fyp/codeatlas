"""The CA-02 scripts: target selection (--repo PATH@SHA) and the export run, BM25 only."""

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from codeatlas.analyze.semantic_gate import DevCriterion

SCRIPTS = Path(__file__).parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    # scripts/ is not a package; the scripts import semantic_target as a sibling module.
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


target = _load("semantic_target")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("D:/data/airflow@4f1c2ab", (Path("D:/data/airflow"), "4f1c2ab")),
        ("D:/data/airflow", (Path("D:/data/airflow"), "HEAD")),
        ("../arrow@main", (Path("../arrow"), "main")),
        ("/srv/me@home/repo", (Path("/srv/me@home/repo"), "HEAD")),  # '@' inside the path
    ],
)
def test_split_spec(spec: str, expected: tuple[Path, str]) -> None:
    assert target.split_spec(spec) == expected


def test_load_target_pins_a_git_repo_at_the_given_commit(tmp_path: Path) -> None:
    pygit2 = pytest.importorskip("pygit2")
    repo = pygit2.init_repository(str(tmp_path))
    sig = pygit2.Signature("Test", "test@example.com")
    (tmp_path / "fees.py").write_text("def late_fee(days):\n    return days * 2\n")
    repo.index.add("fees.py")
    repo.index.write()
    first = repo.create_commit("refs/heads/main", sig, sig, "one", repo.index.write_tree(), [])
    (tmp_path / "fees.py").write_text("def other():\n    return 0\n")
    repo.index.add("fees.py")
    repo.index.write()
    repo.create_commit("refs/heads/main", sig, sig, "two", repo.index.write_tree(), [first])

    loaded = target.load_target(f"{tmp_path}@{str(first)[:8]}")
    assert loaded.pinned_sha == str(first)
    assert loaded.label == tmp_path.name
    assert "fees.late_fee" in {e.entity_id for e in loaded.entities}


def test_load_target_without_repo_reads_codeatlas_src() -> None:
    loaded = target.load_target(None)
    assert (loaded.label, loaded.pinned_sha) == ("codeatlas", "working-tree")
    assert "codeatlas/analyze/semantic_run.py" in loaded.sources


def test_dev_criterion_keeps_key_version_and_text() -> None:
    crit = DevCriterion("AIRFLOW-12:v3:ac2", "Retry the task.", frozenset()).as_criterion()
    assert (crit.key, crit.version_no, crit.text) == ("AIRFLOW-12", 3, "Retry the task.")
    assert DevCriterion("X-1", "t", frozenset()).as_criterion().version_no == 1


def test_export_script_writes_a_run_folder(tmp_path: Path) -> None:
    export = _load("export_semantic_run")
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "criterion_id,criterion_text,entity_id\n"
        "DEV-01:v1:ac1,Split the acceptance criteria text into criteria.,"
        "codeatlas.analyze.criteria.split_criteria\n",
        encoding="utf-8",
    )
    code = export.main(
        [
            "--criteria",
            str(labels),
            "--out",
            str(tmp_path / "runs"),
            "--run-id",
            "t1",
            "--bm25-only",
        ]
    )
    assert code == 0
    run = tmp_path / "runs" / "t1"
    doc = json.loads((run / "semantic-run-v1.json").read_text(encoding="utf-8"))
    assert doc["corpus"]["pinned_sha"] == "working-tree"
    rows = list(csv.DictReader((run / "predictions.csv").open(encoding="utf-8")))
    assert rows and {r["ranker"] for r in rows} == {"bm25", "fused"}
