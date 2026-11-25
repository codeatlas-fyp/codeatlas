"""The run ranked exactly the entity list the labellers saw (CA-02, dataset plan task 21)."""

from dataclasses import replace
from pathlib import Path

import pytest

from codeatlas.analyze.rankers import RankedEntity
from codeatlas.analyze.semantic_universe import check_universe, read_entity_ids
from tests.unit.test_semantic_run import _run


def _ids(result) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(doc.entity_id for doc in result.corpus.docs)


def test_same_list_passes_and_bm25_may_rank_fewer() -> None:
    result = _run()
    check = check_universe(result, _ids(result))
    assert check.ok and check.missing == () and check.extra == ()
    assert {r.name: r.ok for r in check.rankers} == {"bm25": True, "minilm": True, "codet5p": True}
    bm25 = next(o for o in result.outputs if o.name == "bm25")
    assert any(len(r) < check.corpus for r in bm25.rankings.values())  # fewer is fine for BM25


def test_missing_and_extra_entities_fail_the_check() -> None:
    result = _run()
    ids = _ids(result)
    check = check_universe(result, [*ids[1:], "lib.fees.not_in_corpus"])
    assert not check.ok
    assert check.missing == ("lib.fees.not_in_corpus",) and check.extra == (ids[0],)


def test_a_dense_ranker_that_skipped_an_entity_fails() -> None:
    result = _run()
    codet5 = next(o for o in result.outputs if o.name == "codet5p")
    cid = sorted(codet5.rankings)[0]
    codet5.rankings[cid] = codet5.rankings[cid][:-1]  # drop one entity from one criterion
    check = check_universe(result, _ids(result))
    coverage = {r.name: r for r in check.rankers}
    assert not check.ok and coverage["codet5p"].short == (cid,)


def test_a_ranked_entity_outside_the_corpus_fails() -> None:
    result = _run()
    bm25 = next(o for o in result.outputs if o.name == "bm25")
    cid = sorted(bm25.rankings)[0]
    bm25.rankings[cid] = [*bm25.rankings[cid], RankedEntity("ghost.f", 99, 0.0, "ghost.f#0")]
    coverage = {r.name: r for r in check_universe(result, _ids(result)).rankers}
    assert coverage["bm25"].outside == (cid,)


def test_unavailable_rankers_are_reported_with_their_state() -> None:
    result = _run(bm25_only=True)
    check = check_universe(result, _ids(result))
    states = {r.name: (r.state, r.criteria) for r in check.rankers}
    assert check.ok and states["codet5p"] == ("unavailable", 0)


def test_report_is_plain_json_values() -> None:
    result = _run()
    data = check_universe(result, _ids(result)[1:]).as_dict(sample=1)
    assert data["ok"] is False and data["extra"] == 1 and len(data["extra_sample"]) == 1  # type: ignore[arg-type]


def test_read_entity_ids(tmp_path: Path) -> None:
    path = tmp_path / "entities.csv"
    path.write_text("entity_id,label_ref\na.f,a.py::f\na.g,a.py::g\n", encoding="utf-8")
    assert read_entity_ids(path) == ["a.f", "a.g"]
    path.write_text("label_ref\na.py::f\n", encoding="utf-8")
    with pytest.raises(ValueError, match="entity_id"):
        read_entity_ids(path)


def test_skipped_corpus_entities_count_as_missing() -> None:
    result = _run()
    ids = _ids(result)
    smaller = replace(result, corpus=replace(result.corpus, docs=result.corpus.docs[1:]))
    check = check_universe(smaller, ids)
    assert check.missing == (result.corpus.docs[0].entity_id,)
