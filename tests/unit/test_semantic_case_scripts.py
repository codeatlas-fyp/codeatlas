"""Scripts for the 9-case sprint: --root and --entities on the export, frozen case folders,
the code-only embedding precompute and the run over every case (dataset plan tasks 9, 21)."""

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from codeatlas.analyze.embedding_cache import EmbeddingCache
from tests.unit.test_semantic_scripts import _git_repo_with_code, _load

target = _load("semantic_target")
export = _load("export_semantic_run")
frozen = _load("frozen_case")
precompute = _load("precompute_embeddings")
prepare = _load("prepare_case")
runner = _load("run_semantic_cases")

ISSUE = "Late fees.\n\nAcceptance criteria:\n- Charge two per day late.\n- Encrypt the zebra.\n"


def _two_folder_repo(path: Path) -> str:
    """lib/ and tools/ with code, so an analysis root of lib/ leaves tools/ out."""
    sha = _git_repo_with_code(path)
    pygit2 = pytest.importorskip("pygit2")
    repo = pygit2.Repository(str(path))
    (path / "tools").mkdir()
    (path / "tools" / "build.py").write_text("def build_wheel():\n    return 'wheel'\n")
    repo.index.add_all()
    repo.index.write()
    sig = pygit2.Signature("Test", "test@example.com")
    tree = repo.index.write_tree()
    return str(repo.create_commit("refs/heads/main", sig, sig, "two", tree, [sha]))


def _case(tmp_path: Path, case_id: str, repo_name: str, sha: str, root: str) -> Path:
    """A frozen case folder as freeze_case.py and prepare_packets.py leave it."""
    folder = tmp_path / "cases" / case_id
    (folder / "packet").mkdir(parents=True)
    (folder / "case.yaml").write_text(
        f"case_id: {case_id}\nset: pilot\nrepo: apache/{repo_name}\n"
        f"head_at_merge: {sha}           # 2nd parent\nanalysis_root: {root}\n",
        encoding="utf-8",
    )
    issue = tmp_path / f"{case_id}.txt"
    issue.write_text(ISSUE, encoding="utf-8")
    args = ["--repo", f"{tmp_path / 'repos' / repo_name}@{sha}", "--key", f"{case_id}K-1"]
    args += ["--issue-text", str(issue), "--root", root, "--out", str(folder / "packet")]
    assert prepare.main(args) == 0
    return folder


# ---- --root on the target and the export ------------------------------------------------------


def test_load_target_root_keeps_only_that_folder(tmp_path: Path) -> None:
    sha = _two_folder_repo(tmp_path / "repo")
    whole = target.load_target(f"{tmp_path / 'repo'}@{sha}")
    lib = target.load_target(f"{tmp_path / 'repo'}@{sha}", "lib/")
    assert "tools/build.py" in whole.sources and set(lib.sources) == {"lib/fees.py"}
    assert {e.file for e in lib.entities} == {"lib/fees.py"}


def test_under_root() -> None:
    assert target.under_root("python/pyarrow/a.py", "python/")
    assert target.under_root("python/pyarrow/a.py", "/python")
    assert not target.under_root("pythonic/a.py", "python")
    assert target.under_root("any/a.py", "") and target.under_root("any/a.py", "/")


def _export(tmp_path: Path, sha: str, entities: Path, run_id: str) -> int:
    criteria = tmp_path / "criteria.csv"
    criteria.write_text(
        "criterion_id,key,version_no,criterion_text\nLIB-1:v1:ac1,LIB-1,1,Charge late fees.\n",
        encoding="utf-8",
    )
    args = ["--criteria", str(criteria), "--repo", f"{tmp_path / 'repo'}@{sha}"]
    args += ["--root", "lib/", "--entities", str(entities), "--out", str(tmp_path / "runs")]
    return int(export.main([*args, "--run-id", run_id, "--bm25-only"]))


def test_export_checks_the_entity_list_of_the_packet(tmp_path: Path) -> None:
    sha = _two_folder_repo(tmp_path / "repo")
    issue = tmp_path / "issue.txt"
    issue.write_text(ISSUE, encoding="utf-8")
    packet = tmp_path / "packet"
    args = ["--repo", f"{tmp_path / 'repo'}@{sha}", "--key", "LIB-1", "--root", "lib/"]
    assert prepare.main([*args, "--issue-text", str(issue), "--out", str(packet)]) == 0

    assert _export(tmp_path, sha, packet / "entities.csv", "ok") == 0
    check = json.loads((tmp_path / "runs" / "ok" / "universe_check.json").read_text())
    assert check["ok"] is True and check["corpus_entities"] == check["expected_entities"]


def test_export_exits_3_when_the_entity_list_differs(tmp_path: Path) -> None:
    sha = _two_folder_repo(tmp_path / "repo")
    entities = tmp_path / "entities.csv"
    entities.write_text("entity_id,label_ref\ntools.build.build_wheel,x\n", encoding="utf-8")
    assert _export(tmp_path, sha, entities, "bad") == export.EXIT_UNIVERSE_MISMATCH
    check = json.loads((tmp_path / "runs" / "bad" / "universe_check.json").read_text())
    assert check["ok"] is False and check["missing"] == 1 and check["extra"] >= 1


# ---- frozen case folders ------------------------------------------------------------------------


def test_read_case_takes_the_four_facts(tmp_path: Path) -> None:
    folder = tmp_path / "AR-01"
    folder.mkdir()
    (folder / "case.yaml").write_text(
        "case_id: AR-01\nrepo: apache/arrow\npr_number: 13000\n"
        f'head_at_merge: {"a" * 40}   # 2nd parent\nanalysis_root: "python/"\n',
        encoding="utf-8",
    )
    case = frozen.read_case(folder)
    assert (case.case_id, case.repo_name, case.analysis_root) == ("AR-01", "arrow", "python")
    assert case.target_spec(Path("D:/repos")) == f"{Path('D:/repos') / 'arrow'}@{'a' * 40}"
    assert case.criteria_csv == folder / "packet" / "criteria.csv"


def test_read_case_refuses_a_folder_without_a_full_head(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="case.yaml"):
        frozen.read_case(tmp_path)
    (tmp_path / "case.yaml").write_text("repo: apache/x\nhead_at_merge: abc123\n")
    with pytest.raises(ValueError, match="40-character"):
        frozen.read_case(tmp_path)


# ---- the code-only precompute -------------------------------------------------------------------


class CachedFake:
    """A fake dense model that uses the real on-disk cache, like MiniLM and CodeT5+ do."""

    model_id = "fake/cached@1"

    def __init__(self, root: Path) -> None:
        self.cache = EmbeddingCache(root, self.model_id)
        self.computed = 0

    def embed(self, texts: Sequence[str]) -> np.ndarray[Any, Any]:
        rows = []
        for text in texts:
            vec = self.cache.get(text)
            if vec is None:
                self.computed += 1
                vec = np.array([len(text), 1.0], dtype=np.float32)
                vec /= np.linalg.norm(vec)
                self.cache.put(text, vec)
            rows.append(vec)
        return np.array(rows)


def test_precompute_embeds_every_chunk_once_and_reads_no_criteria(tmp_path: Path) -> None:
    sha = _two_folder_repo(tmp_path / "repo")
    model = CachedFake(tmp_path / "cache")
    spec = f"{tmp_path / 'repo'}@{sha}"
    first = precompute.precompute(spec, "lib/", {"codet5p": lambda: model}, batch=1)
    chunks = model.computed
    assert chunks > 0 and f"{chunks} new" in first[1]
    second = precompute.precompute(spec, "lib/", {"codet5p": lambda: model}, batch=1)
    assert model.computed == chunks and f"{chunks} already cached, 0 new" in second[1]


def test_precompute_needs_a_target() -> None:
    with pytest.raises(SystemExit):
        precompute.main([])


def test_precompute_case_needs_repos_dir(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        precompute.main(["--case", str(tmp_path)])


# ---- the run over every case ----------------------------------------------------------------


def _sprint(tmp_path: Path) -> tuple[Path, str]:
    sha = _two_folder_repo(tmp_path / "repos" / "lib")
    _case(tmp_path, "LB-01", "lib", sha, "lib/")
    _case(tmp_path, "LB-02", "lib", sha, "")
    return tmp_path / "cases", sha


def _run(tmp_path: Path, *extra: str) -> int:
    cases, _ = _sprint(tmp_path)
    args = ["--cases-dir", str(cases), "--repos-dir", str(tmp_path / "repos")]
    return int(runner.main([*args, "--out", str(tmp_path / "out"), "--bm25-only", *extra]))


def test_run_writes_one_checked_folder_per_case(tmp_path: Path) -> None:
    assert _run(tmp_path) == 0
    out = tmp_path / "out"
    summary = json.loads((out / "cases_summary.json").read_text(encoding="utf-8"))
    assert [r["case_id"] for r in summary] == ["LB-01", "LB-02"]
    assert all(r["universe_ok"] for r in summary) and summary[0]["analysis_root"] == "lib"
    assert summary[1]["entities"] > summary[0]["entities"]  # whole repo vs lib/ only
    rows = list(csv.DictReader((out / "LB-01" / "rankings.csv").open(encoding="utf-8")))
    assert {r["run_id"] for r in rows} == {"LB-01"} and {r["repo"] for r in rows} == {"lib"}
    assert "| LB-01 | lib | lib |" in (out / "cases_summary.md").read_text(encoding="utf-8")


def test_run_scores_the_cases_with_labels(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "criterion_id,criterion_text,entity_id\n"
        "LB-01K-1:v1:ac1,Charge two per day late.,lib/fees.py::Fees.late\n"
        "LB-01K-1:v1:ac2,Encrypt the zebra.,\n"
        "LB-02K-1:v1:ac1,Charge two per day late.,lib/fees.py::Fees.late\n",
        encoding="utf-8",
    )
    assert _run(tmp_path, "--labels", str(labels)) == 0
    data = json.loads((tmp_path / "out" / "evaluation.json").read_text(encoding="utf-8"))
    assert data["run_ids"] == ["LB-01", "LB-02"] and data["not_scored"] == []
    bm25 = next(r for r in data["rankers"] if r["ranker"] == "bm25")
    assert bm25["cases"] == 2 and bm25["criteria"] == 2


def test_a_case_whose_check_fails_is_not_scored(tmp_path: Path) -> None:
    cases, _ = _sprint(tmp_path)
    entities = cases / "LB-02" / "packet" / "entities.csv"
    lines = entities.read_text(encoding="utf-8").splitlines()
    entities.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # one entity dropped
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "criterion_id,criterion_text,entity_id\n"
        "LB-01K-1:v1:ac1,Charge two per day late.,lib/fees.py::Fees.late\n",
        encoding="utf-8",
    )
    args = ["--cases-dir", str(cases), "--repos-dir", str(tmp_path / "repos")]
    args += ["--out", str(tmp_path / "out"), "--bm25-only", "--labels", str(labels)]
    assert runner.main(args) == runner.EXIT_CHECK_FAILED
    data = json.loads((tmp_path / "out" / "evaluation.json").read_text(encoding="utf-8"))
    assert data["not_scored"] == ["LB-02"] and data["run_ids"] == ["LB-01"]
    text = (tmp_path / "out" / "evaluation.md").read_text(encoding="utf-8")
    assert "Not scored (entity list check failed): LB-02" in text


def test_run_refuses_a_case_without_a_packet(tmp_path: Path) -> None:
    cases, _ = _sprint(tmp_path)
    (cases / "LB-01" / "packet" / "criteria.csv").unlink()
    args = ["--cases-dir", str(cases), "--repos-dir", str(tmp_path / "repos")]
    assert runner.main([*args, "--out", str(tmp_path / "out"), "--bm25-only"]) == 1


def test_run_needs_cases() -> None:
    with pytest.raises(SystemExit):
        runner.main(["--repos-dir", "x"])
