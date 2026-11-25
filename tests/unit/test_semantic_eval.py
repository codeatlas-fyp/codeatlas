"""Evaluation on full rankings: median rank, uncut MRR, recall@k curve, gate, Wilson (CA-02)."""

import math

import pytest

from codeatlas.analyze.semantic_eval import (
    KS,
    Rate,
    as_dict,
    case_bootstrap,
    evaluate,
    markdown,
    recall_curve_csv,
    recall_curve_svg,
)
from codeatlas.analyze.semantic_gate import DevCriterion

LABELS = [
    DevCriterion("C1:v1:ac1", "fees", frozenset({"a.fee"})),
    DevCriterion("C2:v1:ac1", "books", frozenset({"a.book", "a.shelf"})),
    DevCriterion("C3:v1:ac1", "zebra", frozenset()),  # no match
]


def _row(cid: str, ranker: str, rank: int, entity: str, gate: str, repo: str = "lib") -> dict:
    return {
        "run_id": "r1",
        "repo": repo,
        "criterion_id": cid,
        "ranker": ranker,
        "rank": str(rank),
        "entity_id": entity,
        "gate": gate,
    }


def _ranking(cid: str, ranker: str, entities: list[str], gate: str, repo: str = "lib") -> list:
    return [_row(cid, ranker, i, e, gate, repo) for i, e in enumerate(entities, start=1)]


FILLER = [f"a.x{i}" for i in range(30)]
ROWS = [
    # codet5p: C1 right at rank 1; C2's a.shelf far down at rank 23, a.book not ranked at all
    *_ranking("C1:v1:ac1", "codet5p", ["a.fee", *FILLER[:5]], "accept"),
    *_ranking("C2:v1:ac1", "codet5p", [*FILLER[:22], "a.shelf"], "review"),
    *_ranking("C3:v1:ac1", "codet5p", FILLER[:5], "abstain"),
    # bm25 never ranked a.fee (no shared word); C2 both relevant in its top 2
    *_ranking("C1:v1:ac1", "bm25", FILLER[:3], "accept"),
    *_ranking("C2:v1:ac1", "bm25", ["a.book", "a.shelf"], "review"),
    # fused
    *_ranking("C1:v1:ac1", "fused", ["a.fee", *FILLER[:3]], "accept"),
    *_ranking("C2:v1:ac1", "fused", ["a.book", "a.shelf"], "review"),
    *_ranking("C3:v1:ac1", "fused", FILLER[:3], "abstain"),
]


def _score(ranker: str):  # type: ignore[no-untyped-def]
    return next(s for s in evaluate(ROWS, LABELS).rankers if s.ranker == ranker)


def test_rank_is_read_from_the_full_ranking_not_a_top_k() -> None:
    codet5 = _score("codet5p")
    assert codet5.criteria == 2  # C3 has nothing to find
    assert codet5.first_ranks == (1, 23)  # rank 23 is still seen
    assert codet5.median_rank == 12.0
    assert codet5.mrr == pytest.approx((1 + 1 / 23) / 2, abs=1e-4)
    assert codet5.recall[1] == 0.5 and codet5.recall[20] == 0.5
    assert codet5.recall[50] == 0.75  # C2: a.shelf found by 50, a.book never
    assert (codet5.hit[5].hits, codet5.hit[50].hits) == (1, 2)


def test_entity_a_ranker_never_ranked_counts_as_not_ranked() -> None:
    bm25 = _score("bm25")
    assert bm25.first_ranks == (None, 1)
    assert bm25.not_ranked == 1
    assert bm25.mrr == 0.5
    # median of [not ranked, 1] is the mean of the two middle values, which is infinite
    assert bm25.median_rank is None


def test_rankers_are_reported_in_a_fixed_order_and_ks_are_wide() -> None:
    assert [s.ranker for s in evaluate(ROWS, LABELS).rankers] == ["bm25", "codet5p", "fused"]
    assert KS == (1, 5, 10, 20, 50)


def test_gate_scores_use_score_decisions_not_positions() -> None:
    gate = evaluate(ROWS, LABELS).gate
    assert (gate.no_match_abstained.hits, gate.no_match_abstained.n) == (1, 1)
    assert (gate.match_abstained.hits, gate.match_abstained.n) == (0, 2)
    assert (gate.accept_correct.hits, gate.accept_correct.n) == (1, 1)


def test_wilson_interval_matches_the_plan_example() -> None:
    low, high = Rate(90, 100).wilson()  # type: ignore[misc]
    assert (round(low, 2), round(high, 2)) == (0.83, 0.94)
    assert Rate(0, 0).wilson() is None and "not estimable" in Rate(0, 0).text()
    low, high = Rate(0, 5).wilson()  # type: ignore[misc]
    assert low == 0.0 and 0 < high < 1


def test_per_repository_scores_are_kept_apart() -> None:
    rows = [r for r in ROWS if r["criterion_id"] != "C2:v1:ac1"]
    rows += [{**r, "repo": "other"} for r in ROWS if r["criterion_id"] == "C2:v1:ac1"]
    result = evaluate(rows, LABELS)
    repos = {(s.repo, s.ranker): s.criteria for s in result.per_repo}
    assert repos[("lib", "codet5p")] == 1 and repos[("other", "codet5p")] == 1
    assert "Per repository" in markdown(result)


def test_missing_and_unlabelled_criteria_are_listed() -> None:
    labels = [*LABELS, DevCriterion("C9:v1:ac1", "never run", frozenset({"a.z"}))]
    rows = [*ROWS, _row("C7:v1:ac1", "fused", 1, "a.q", "review")]
    result = evaluate(rows, labels)
    assert result.missing == ("C9:v1:ac1",)
    assert result.unlabelled == ("C7:v1:ac1",)


def test_markdown_json_and_curve_views() -> None:
    result = evaluate(ROWS, LABELS)
    text = markdown(result)
    assert "median rank" in text and "recall@50" in text and "Wilson" in text
    assert "no-match criteria abstained (higher is better) | 1/1" in text
    data = as_dict(result)
    assert data["ks"] == [1, 5, 10, 20, 50]
    codet5 = data["rankers"][1]  # type: ignore[index]
    assert codet5["first_ranks"] == [1, 23] and codet5["hit"]["50"]["n"] == 2
    csv_text = recall_curve_csv(result)
    assert csv_text.splitlines()[0] == "ranker,recall@1,recall@5,recall@10,recall@20,recall@50"
    svg = recall_curve_svg(result)
    assert svg.startswith("<svg") and svg.count("<polyline") == 3
    assert not math.isnan(codet5["mrr"])


def test_precision_recall_and_f1_of_the_top_k() -> None:
    s = _score("codet5p")  # C1: a.fee at rank 1; C2: nothing relevant in the top 10
    assert (s.precision[1], s.recall[1], s.f1[1]) == (0.5, 0.5, 0.5)
    assert s.precision[5] == 0.1  # C1: 1 of 5, C2: 0 of 5
    assert s.f1[5] == round((2 * 0.2 * 1.0 / 1.2 + 0.0) / 2, 4)


def _two_cases() -> list[dict]:
    rows = [dict(r, run_id="AF-01") for r in ROWS if r["criterion_id"] == "C1:v1:ac1"]
    return rows + [dict(r, run_id="AR-01") for r in ROWS if r["criterion_id"] != "C1:v1:ac1"]


def test_one_case_has_no_bootstrap_interval() -> None:
    s = _score("codet5p")  # every row is run r1
    assert s.cases == 1 and s.bootstrap["mrr"] is None


def test_bootstrap_resamples_whole_cases_and_is_reproducible() -> None:
    first = next(s for s in evaluate(_two_cases(), LABELS).rankers if s.ranker == "codet5p")
    again = next(s for s in evaluate(_two_cases(), LABELS).rankers if s.ranker == "codet5p")
    assert first.cases == 2 and first.bootstrap == again.bootstrap
    low, high = first.bootstrap["mrr"]  # type: ignore[misc]
    # AF-01 has MRR 1 (C1), AR-01 has 1/23 (C2): a draw is one of three means
    assert low <= first.mrr <= high and low >= 1 / 23 - 1e-9 and high <= 1.0


def test_case_bootstrap_bounds() -> None:
    assert case_bootstrap({"a": [0.5, 0.5], "b": [0.5]}) == (0.5, 0.5)
    low, high = case_bootstrap({"a": [1.0], "b": [0.0]}, rounds=200)  # type: ignore[misc]
    assert 0.0 <= low <= 0.5 <= high <= 1.0
    assert case_bootstrap({"a": [1.0], "b": []}) is None  # one case with criteria


def test_a_criterion_in_two_runs_is_refused() -> None:
    rows = ROWS + [dict(_row("C1:v1:ac1", "codet5p", 1, "a.fee", "accept"), run_id="r2")]
    with pytest.raises(ValueError, match="one case"):
        evaluate(rows, LABELS)


def test_new_measures_appear_in_markdown_and_json() -> None:
    result = evaluate(_two_cases(), LABELS)
    text = markdown(result)
    assert "bootstrap interval resampling whole cases (2 cases" in text and "F1@5" in text
    data = as_dict(result)
    codet5 = next(r for r in data["rankers"] if r["ranker"] == "codet5p")  # type: ignore[union-attr]
    assert codet5["cases"] == 2 and codet5["bootstrap95"]["mrr"] is not None
    assert set(codet5["f1"]) == {"1", "5", "10"} and data["bootstrap"]["resampled"] == "cases"
