"""Evaluation on full rankings: median rank, uncut MRR, recall@k curve, gate, Wilson (CA-02)."""

import math

import pytest

from codeatlas.analyze.semantic_eval import (
    KS,
    Rate,
    as_dict,
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
