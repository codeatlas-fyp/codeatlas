"""semantic-run-v1 export: the JSON record and the predictions CSV (CA-02)."""

import csv
import io
import json
from pathlib import Path

from codeatlas.analyze.semantic_export import (
    CSV_COLUMNS,
    export_document,
    prediction_rows,
    predictions_csv,
    write_export,
)
from codeatlas.schema import SemanticRunSummary
from tests.unit.test_semantic_run import FEE, NOTHING, SHA, _run

CRITERIA = [FEE, NOTHING]


def test_document_holds_summary_models_corpus_and_verbatim_criteria() -> None:
    result = _run()
    doc = export_document(result, CRITERIA, run_id="r1", repo="lib")
    assert doc["schema_id"] == "semantic-run-v1"
    assert (doc["run_id"], doc["repo"]) == ("r1", "lib")
    assert SemanticRunSummary.model_validate(doc["summary"]) == result.summary
    assert doc["model_file_hashes"] == {"fake/bow@1": "0" * 64}
    assert doc["corpus"]["pinned_sha"] == SHA
    assert doc["corpus"]["entities"] == len(result.corpus.docs)
    assert [c["text"] for c in doc["criteria"]] == [FEE.text, NOTHING.text]
    assert [r["name"] for r in doc["rankers"]] == ["bm25", "minilm", "codet5p"]
    assert len(doc["candidates"]) == len(result.candidates)


def test_infinite_threshold_is_written_as_null() -> None:
    doc = export_document(_run(bm25_only=True), CRITERIA, run_id="r", repo="lib")
    assert doc["summary"]["accept_threshold"] is None
    json.dumps(doc, allow_nan=False)  # strict JSON: no Infinity / NaN


def test_rows_carry_rank_span_gate_and_model_for_every_ranker() -> None:
    result = _run()
    rows = prediction_rows(result, CRITERIA, run_id="r1", repo="lib", top_n=2)
    fee = [r for r in rows if r["criterion_id"] == FEE.criterion_id]
    assert {r["ranker"] for r in fee} == {"bm25", "minilm", "codet5p", "fused"}
    codet5 = [r for r in fee if r["ranker"] == "codet5p"]
    assert [r["rank"] for r in codet5] == ["1", "2"]  # top_n respected
    first = codet5[0]
    assert first["entity_id"] == "lib.fees.calculate_late_fee"
    assert first["file"] == "lib/fees.py"
    assert int(first["start_line"]) <= int(first["end_line"])
    assert first["gate"] == "accept" and first["pinned_sha"] == SHA
    assert first["criterion_text"] == FEE.text and first["criterion_version"] == "1"


def test_csv_has_fixed_columns_and_round_trips() -> None:
    rows = prediction_rows(_run(), CRITERIA, run_id="r1", repo="lib")
    text = predictions_csv(rows)
    assert text.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert list(csv.DictReader(io.StringIO(text))) == rows


def test_write_export_creates_both_files(tmp_path: Path) -> None:
    json_path, csv_path = write_export(tmp_path / "r1", _run(), CRITERIA, run_id="r1", repo="lib")
    assert json_path.name == "semantic-run-v1.json" and csv_path.name == "predictions.csv"
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == "r1"
    assert csv_path.read_text(encoding="utf-8").startswith("run_id,repo,pinned_sha")


def test_export_is_deterministic(tmp_path: Path) -> None:
    a = write_export(tmp_path / "a", _run(), CRITERIA, run_id="r", repo="lib")
    b = write_export(tmp_path / "b", _run(), CRITERIA, run_id="r", repo="lib")
    assert a[1].read_bytes() == b[1].read_bytes()


def test_rankings_csv_holds_every_ranked_entity_and_the_full_fused_list(tmp_path: Path) -> None:
    from codeatlas.analyze.semantic_export import RANKINGS_COLUMNS, RANKINGS_NAME, ranking_rows

    result = _run(top_k=1)  # the bundle shows one candidate per criterion
    rows = ranking_rows(result, CRITERIA, run_id="r1", repo="lib")
    fee = [r for r in rows if r["criterion_id"] == FEE.criterion_id]
    n = len(result.corpus.docs)
    assert len([r for r in fee if r["ranker"] == "codet5p"]) == n  # dense ranks everything
    fused = [r for r in fee if r["ranker"] == "fused"]
    assert len(fused) == n > 1  # full RRF list, not the single shown candidate
    assert [r["rank"] for r in fused] == [str(i) for i in range(1, n + 1)]
    assert {r["entities_in_corpus"] for r in rows} == {str(n)}
    write_export(tmp_path / "r1", result, CRITERIA, run_id="r1", repo="lib")
    header = (tmp_path / "r1" / RANKINGS_NAME).read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(RANKINGS_COLUMNS)
