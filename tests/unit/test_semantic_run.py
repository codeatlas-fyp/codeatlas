"""One semantic run end to end: corpus, three rankers, fusion, gate, summary (CA-02)."""

import math

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.codet5p import EmbedderUnavailable
from codeatlas.analyze.semantic_gate import GateCalibration, Thresholds
from codeatlas.analyze.semantic_run import SCHEMA_ID, default_loaders, run_semantic
from codeatlas.schema import Criterion, SemanticRunSummary
from tests.unit.test_rankers import SOURCE, BagOfWords

SHA = "c" * 40
FILES = {"lib/fees.py": SOURCE}
CAL = GateCalibration(
    calibration_id="gate@test",
    provisional=True,
    order=("codet5p", "minilm"),
    thresholds={"codet5p": Thresholds(0.5, 0.2), "minilm": Thresholds(0.5, 0.2)},
)
FEE = Criterion(
    criterion_id="SBX-1:v1:ac1",
    key="SBX-1",
    version_no=1,
    text="charge a late fee when overdue",
    derived_from_description=False,
)
NOTHING = Criterion(
    criterion_id="SBX-2:v1:ac1",
    key="SBX-2",
    version_no=1,
    text="encrypt the zebra",
    derived_from_description=False,
)


def _unavailable() -> BagOfWords:
    raise EmbedderUnavailable("OSError: offline")


def _run(**kwargs):  # type: ignore[no-untyped-def]
    args = {
        "criteria": [FEE, NOTHING],
        "entities": extract_entities(file_path="lib/fees.py", source=SOURCE),
        "file_sources": FILES,
        "pinned_sha": SHA,
        "top_k": 5,
        "calibration": CAL,
        "loaders": {"minilm": BagOfWords, "codet5p": BagOfWords},
    }
    args.update(kwargs)
    return run_semantic(**args)


def test_full_run_ranks_gates_and_summarises() -> None:
    result = _run()
    summary = result.summary
    assert summary.schema_id == SCHEMA_ID
    assert summary.pinned_sha == SHA and summary.corpus_hash == result.corpus.corpus_hash
    assert [(r.name, r.state) for r in summary.rankers] == [
        ("bm25", "ok"),
        ("minilm", "ok"),
        ("codet5p", "ok"),
    ]
    assert summary.calibration_id == "gate@test (provisional)"
    assert (summary.accept_threshold, summary.review_threshold) == (0.5, 0.2)
    top = [c for c in result.candidates if c.criterion_id == FEE.criterion_id][0]
    assert top.entity_id == "lib.fees.calculate_late_fee"
    assert top.codet5_rank == 1 and top.dense_rank == 1 and top.bm25_rank == 1
    assert top.gate == "accept"
    assert [g.criterion_id for g in result.gates] == [FEE.criterion_id, NOTHING.criterion_id]
    assert summary.gates == result.gates
    SemanticRunSummary.model_validate(summary.model_dump())  # survives a round trip


def test_model_ids_and_weight_hashes_name_what_really_ran() -> None:
    result = _run()
    assert result.model_ids == sorted(["bm25@rank-bm25-okapi", "fake/bow@1", "fake/bow@1"])
    assert result.model_file_hashes == {"fake/bow@1": "0" * 64}


def test_bm25_only_records_dense_rankers_as_disabled() -> None:
    result = _run(bm25_only=True)
    states = {r.name: (r.state, r.reason) for r in result.summary.rankers}
    assert states["minilm"] == ("unavailable", "disabled (bm25_only)")
    assert states["codet5p"] == ("unavailable", "disabled (bm25_only)")
    assert result.model_file_hashes == {}
    assert math.isinf(result.summary.accept_threshold)  # no calibrated gate ranker ran
    assert all(g.gate in ("review", "abstain") for g in result.gates)  # BM25 never accepts


def test_codet5p_down_falls_back_to_minilm_gate() -> None:
    result = _run(loaders={"minilm": BagOfWords, "codet5p": _unavailable})
    codet5 = next(r for r in result.summary.rankers if r.name == "codet5p")
    assert (codet5.state, codet5.reason) == ("unavailable", "OSError: offline")
    fee = next(g for g in result.gates if g.criterion_id == FEE.criterion_id)
    assert "gate ranker minilm" in fee.reason
    assert all(c.codet5_rank is None for c in result.candidates)


def test_missing_loader_is_reported_not_skipped_silently() -> None:
    result = _run(loaders={})
    reasons = {r.name: r.reason for r in result.summary.rankers}
    assert reasons["minilm"] == reasons["codet5p"] == "no loader configured"


def test_no_criteria_gives_no_candidates_and_no_model_ids() -> None:
    result = _run(criteria=[])
    assert result.candidates == [] and result.gates == [] and result.model_ids == []


def test_run_is_deterministic() -> None:
    assert _run().summary == _run().summary
    assert _run().candidates == _run().candidates


def test_default_loaders_cover_both_dense_rankers() -> None:
    assert sorted(default_loaders()) == ["codet5p", "minilm"]
