"""Accept / review / abstain gate and its calibration (CA-02)."""

import math
from pathlib import Path

from codeatlas.analyze.rankers import RankedEntity, RankerOutput
from codeatlas.analyze.semantic_gate import (
    PROVISIONAL,
    DevCriterion,
    GateCalibration,
    Thresholds,
    apply_gate,
    calibrate,
    decide,
    load_calibration,
    read_dev_labels,
)
from codeatlas.schema import SemanticCandidate

CAL = GateCalibration(
    calibration_id="gate@test",
    provisional=True,
    order=("codet5p", "minilm"),
    thresholds={"codet5p": Thresholds(0.6, 0.4), "minilm": Thresholds(0.5, 0.3)},
)


def _out(name: str, ranking: dict[str, list[tuple[str, float]]], state: str = "ok") -> RankerOutput:
    out = RankerOutput(name, f"{name}@1", state, None)  # type: ignore[arg-type]
    out.rankings = {
        cid: [RankedEntity(e, i, s, f"{e}#0") for i, (e, s) in enumerate(items, start=1)]
        for cid, items in ranking.items()
    }
    return out


def test_accept_needs_score_and_agreement() -> None:
    outputs = [
        _out("codet5p", {"C1": [("a.f", 0.7)]}),
        _out("bm25", {"C1": [("a.f", 9.0)]}),
    ]
    (gate,) = decide(["C1"], outputs, CAL)
    assert (gate.gate, gate.top_score) == ("accept", 0.7)
    assert "2 rankers agree" in gate.reason


def test_high_score_without_agreement_is_review() -> None:
    outputs = [_out("codet5p", {"C1": [("a.f", 0.7)]}), _out("bm25", {"C1": [("b.g", 9.0)]})]
    (gate,) = decide(["C1"], outputs, CAL)
    assert (gate.gate, gate.reason) == ("review", "score 0.7; rankers disagree")


def test_middle_score_is_review_and_low_score_abstains() -> None:
    outputs = [_out("codet5p", {"C1": [("a.f", 0.5)], "C2": [("a.f", 0.1)]})]
    review, abstain = decide(["C1", "C2"], outputs, CAL)
    assert review.gate == "review" and "below 0.6" in review.reason
    assert abstain.gate == "abstain" and "no confident match" in abstain.reason


def test_never_accept_calibration_says_so_in_the_reason() -> None:
    never = GateCalibration(
        calibration_id="gate@never",
        provisional=True,
        order=("codet5p",),
        thresholds={"codet5p": Thresholds(math.inf, 0.4)},
    )
    outputs = [_out("codet5p", {"C1": [("a.f", 0.9)]}), _out("bm25", {"C1": [("a.f", 9.0)]})]
    (gate,) = decide(["C1"], outputs, never)
    assert gate.gate == "review"
    assert gate.reason == "score 0.9; this calibration never auto-accepts"


def test_falls_back_to_minilm_when_codet5p_is_unavailable() -> None:
    outputs = [
        _out("codet5p", {}, state="unavailable"),
        _out("minilm", {"C1": [("a.f", 0.55)]}),
        _out("bm25", {"C1": [("a.f", 3.0)]}),
    ]
    (gate,) = decide(["C1"], outputs, CAL)
    assert gate.gate == "accept"
    assert "gate ranker minilm: codet5p unavailable" in gate.reason


def test_bm25_alone_is_never_accepted() -> None:
    hit, miss = decide(["C1", "C2"], [_out("bm25", {"C1": [("a.f", 50.0)], "C2": []})], CAL)
    assert (hit.gate, miss.gate) == ("review", "abstain")


def test_no_ranker_at_all_is_unavailable() -> None:
    (gate,) = decide(["C1"], [_out("codet5p", {}, state="unavailable")], CAL)
    assert (gate.gate, gate.top_score) == ("unavailable", None)


def test_empty_ranking_abstains() -> None:
    (gate,) = decide(["C1"], [_out("codet5p", {"C1": []})], CAL)
    assert (gate.gate, gate.reason) == ("abstain", "no candidates")


def test_apply_gate_stamps_candidates() -> None:
    cand = SemanticCandidate(
        criterion_id="C1",
        entity_id="a.f",
        bm25_rank=1,
        dense_rank=None,
        fused_rank=1,
        fused_score=0.0164,
        model_id="m",
    )
    outputs = [_out("codet5p", {"C1": [("a.f", 0.1)]})]
    (stamped,) = apply_gate([cand], decide(["C1"], outputs, CAL))
    assert stamped.gate == "abstain"
    (none,) = apply_gate([cand], decide(["C1"], [], CAL))
    assert none.gate is None


def test_calibrate_fits_thresholds_from_dev_labels() -> None:
    # Five matches scoring 0.80..0.60 with the right top-1; two matches whose right entity is
    # only 2nd (0.55, 0.50); four no-match criteria scoring 0.20..0.35.
    dev = [DevCriterion(f"M{i}", "t", frozenset({"good"})) for i in range(7)] + [
        DevCriterion(f"N{i}", "t", frozenset()) for i in range(4)
    ]
    items = {f"M{i}": [("good", 0.8 - 0.05 * i)] for i in range(5)}
    items["M5"] = [("bad", 0.55), ("good", 0.54)]
    items["M6"] = [("bad", 0.50), ("good", 0.49)]
    items |= {f"N{i}": [("bad", 0.2 + 0.05 * i)] for i in range(4)}
    calibration = calibrate(dev, [_out("codet5p", items)], calibration_id="gate@unit")
    limits = calibration.thresholds["codet5p"]
    assert limits.accept == 0.6  # lowest score where top-1 precision >= 0.9 with support 3
    # Below 0.6 two of six still have the right entity in the top 5 (33% > 20%); below 0.55
    # only one of five (20%): so 0.55..0.6 is the review band and below it the gate abstains.
    assert limits.review == 0.55
    assert calibration.order == ("codet5p",)
    report = calibration.report["codet5p"]
    assert (report["accepted"], report["accepted_precision"]) == (5.0, 1.0)
    assert (report["abstained"], report["abstained_with_hit"]) == (5.0, 1.0)


def test_clean_split_leaves_no_review_band() -> None:
    dev = [DevCriterion(f"M{i}", "t", frozenset({"good"})) for i in range(3)] + [
        DevCriterion("N0", "t", frozenset())
    ]
    items = {f"M{i}": [("good", 0.8)] for i in range(3)} | {"N0": [("bad", 0.2)]}
    limits = calibrate(dev, [_out("codet5p", items)], calibration_id="x").thresholds["codet5p"]
    assert limits == Thresholds(0.8, 0.8)


def test_ranker_that_never_reaches_precision_never_accepts() -> None:
    dev = [DevCriterion(f"M{i}", "t", frozenset({"good"})) for i in range(4)]
    calibration = calibrate(
        dev, [_out("minilm", {f"M{i}": [("bad", 0.9)] for i in range(4)})], calibration_id="x"
    )
    assert math.isinf(calibration.thresholds["minilm"].accept)


def test_calibration_round_trips_through_json_with_infinity(tmp_path: Path) -> None:
    never = GateCalibration(
        calibration_id="gate@x",
        provisional=False,
        order=("minilm",),
        thresholds={"minilm": Thresholds(math.inf, 0.3)},
    )
    path = tmp_path / "gate.json"
    path.write_text(never.to_json(), encoding="utf-8")
    assert '"accept": null' in path.read_text(encoding="utf-8")
    assert load_calibration(path) == never


def test_missing_calibration_file_gives_the_provisional_default(tmp_path: Path) -> None:
    assert load_calibration(tmp_path / "none.json") is PROVISIONAL
    assert PROVISIONAL.provisional is True


def test_read_dev_labels_groups_rows_and_keeps_no_match(tmp_path: Path) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(
        "criterion_id,criterion_text,entity_id\n"
        "A:v1:ac1,Close the loan.,lib.loans.close\n"
        "A:v1:ac1,Close the loan.,lib.loans.Loan.close\n"
        'B:v1:ac1,"Send a digest, weekly.",\n',
        encoding="utf-8",
    )
    a, b = read_dev_labels(path)
    assert a.relevant == {"lib.loans.close", "lib.loans.Loan.close"}
    assert (b.text, b.relevant) == ("Send a digest, weekly.", frozenset())


def test_committed_dev_labels_parse() -> None:
    labels = Path(__file__).parents[2] / "calibration" / "dev-codeatlas-self.csv"
    dev = read_dev_labels(labels)
    assert len(dev) == 24
    assert sum(1 for d in dev if not d.relevant) == 5
