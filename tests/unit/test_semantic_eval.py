"""recall@k, hit@k, MRR and gate scores from predictions.csv rows and labels (CA-02)."""

import pytest

from codeatlas.analyze.semantic_eval import as_dict, evaluate, markdown
from codeatlas.analyze.semantic_gate import DevCriterion

LABELS = [
    DevCriterion("C1:v1:ac1", "fees", frozenset({"a.fee"})),
    DevCriterion("C2:v1:ac1", "books", frozenset({"a.book", "a.shelf"})),
    DevCriterion("C3:v1:ac1", "zebra", frozenset()),  # no match
]


def _row(cid: str, ranker: str, rank: int, entity: str, gate: str) -> dict[str, str]:
    return {
        "run_id": "r1",
        "criterion_id": cid,
        "ranker": ranker,
        "rank": str(rank),
        "entity_id": entity,
        "gate": gate,
    }


ROWS = [
    # codet5p: C1 right at rank 1; C2 finds a.shelf at rank 3 only
    _row("C1:v1:ac1", "codet5p", 1, "a.fee", "accept"),
    _row("C1:v1:ac1", "codet5p", 2, "a.x", "accept"),
    _row("C2:v1:ac1", "codet5p", 2, "a.y", "review"),  # out of order on purpose
    _row("C2:v1:ac1", "codet5p", 1, "a.x", "review"),
    _row("C2:v1:ac1", "codet5p", 3, "a.shelf", "review"),
    _row("C3:v1:ac1", "codet5p", 1, "a.x", "abstain"),
    # bm25: C1 misses, C2 both relevant in top 2
    _row("C1:v1:ac1", "bm25", 1, "a.x", "accept"),
    _row("C2:v1:ac1", "bm25", 1, "a.book", "review"),
    _row("C2:v1:ac1", "bm25", 2, "a.shelf", "review"),
    # fused top-1 per criterion
    _row("C1:v1:ac1", "fused", 1, "a.fee", "accept"),
    _row("C2:v1:ac1", "fused", 1, "a.book", "review"),
    _row("C3:v1:ac1", "fused", 1, "a.x", "abstain"),
]


def _score(ranker: str):  # type: ignore[no-untyped-def]
    return next(s for s in evaluate(ROWS, LABELS).rankers if s.ranker == ranker)


def test_ranking_metrics_count_only_criteria_with_a_right_answer() -> None:
    codet5 = _score("codet5p")
    assert codet5.criteria == 2  # C3 has nothing to find
    assert codet5.mrr == pytest.approx((1 / 1 + 1 / 3) / 2, abs=1e-4)
    assert codet5.recall[1] == 0.5  # C1 full, C2 none
    assert codet5.recall[5] == 0.75  # C1 1/1, C2 1/2
    assert codet5.hit[5] == 1.0


def test_a_ranker_with_no_rows_for_a_criterion_scores_zero_there() -> None:
    bm25 = _score("bm25")
    assert bm25.mrr == 0.5  # C1: miss -> 0; C2: rank 1 -> 1
    assert bm25.recall[5] == 0.5


def test_rankers_are_reported_in_a_fixed_order() -> None:
    assert [s.ranker for s in evaluate(ROWS, LABELS).rankers] == ["bm25", "codet5p", "fused"]


def test_gate_scores_abstain_on_no_match_and_accept_precision() -> None:
    gate = evaluate(ROWS, LABELS).gate
    assert (gate.no_match_abstained, gate.no_match_criteria) == (1, 1)
    assert (gate.match_abstained, gate.match_criteria) == (0, 2)
    assert (gate.accepted_correct, gate.accepted) == (1, 1)


def test_missing_and_unlabelled_criteria_are_listed() -> None:
    labels = [*LABELS, DevCriterion("C9:v1:ac1", "never run", frozenset({"a.z"}))]
    rows = [*ROWS, _row("C7:v1:ac1", "fused", 1, "a.q", "review")]
    result = evaluate(rows, labels)
    assert result.missing == ("C9:v1:ac1",)
    assert result.unlabelled == ("C7:v1:ac1",)


def test_markdown_and_json_views() -> None:
    result = evaluate(ROWS, LABELS)
    text = markdown(result)
    assert "| codet5p | 2 |" in text and "MRR" in text
    assert "no-match criteria abstained | 1 / 1" in text
    data = as_dict(result)
    assert data["run_ids"] == ["r1"]
    assert data["rankers"][1]["recall"]["5"] == 0.75  # type: ignore[index]
