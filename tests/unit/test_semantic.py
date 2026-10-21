"""Tests for `analyze.semantic.rank_candidates`.

All tests use `bm25_only=True` so no model download / network needed in CI.
The dense branch is tested separately by the `show_semantic` demo if the
reviewer runs it manually.
"""

from __future__ import annotations

import pytest

from codeatlas.analyze.semantic import rank_candidates
from codeatlas.schema import CodeEntity, Criterion


def _entity(entity_id: str, kind: str, signature: str | None, docstring: str | None) -> CodeEntity:
    return CodeEntity(
        entity_id=entity_id,
        kind=kind,
        file=f"{entity_id.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        signature=signature,
        docstring=docstring,
    )


def _criterion(crit_id: str, text: str) -> Criterion:
    key = crit_id.split(":")[0]
    return Criterion(
        criterion_id=crit_id,
        key=key,
        version_no=1,
        text=text,
        derived_from_description=False,
    )


def test_bm25_ranks_matching_entity_first() -> None:
    entities = [
        _entity(
            "pkg.fees.calculate_late_fee",
            "function",
            "def calculate_late_fee(days_overdue: int) -> Decimal",
            "Calculate the late fee for overdue books.",
        ),
        _entity(
            "pkg.books.Catalog.add",
            "method",
            "def add(self, book: Book) -> None",
            "Add a book to the catalog.",
        ),
        _entity(
            "pkg.loans.Loan.mark_returned",
            "method",
            "def mark_returned(self) -> None",
            "Flag the loan as returned.",
        ),
    ]
    criteria = [_criterion("SBX-4:v1:ac1", "fees for overdue late books")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)
    assert len(result.candidates) == 1
    assert all(c.bm25_rank is not None or c.dense_rank is not None for c in result.candidates)
    # Fee function should rank first (fused_rank == 1).
    first = result.candidates[0]
    assert first.entity_id == "pkg.fees.calculate_late_fee"
    assert first.fused_rank == 1


def test_fused_rank_is_contiguous_and_starts_at_one() -> None:
    entities = [_entity(f"pkg.a.fn{i}", "function", None, "something generic") for i in range(5)]
    criteria = [_criterion("X-1:v1:ac1", "generic query")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True, top_k=3)
    ranks = [c.fused_rank for c in result.candidates if c.criterion_id == "X-1:v1:ac1"]
    assert ranks == [1, 2, 3]


def test_top_k_caps_result_count_per_criterion() -> None:
    entities = [_entity(f"pkg.a.fn{i}", "function", None, "matching text") for i in range(10)]
    criteria = [_criterion("X-1:v1:ac1", "matching text")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True, top_k=5)
    assert len(result.candidates) == 5


def test_module_entities_are_not_ranked() -> None:
    entities = [
        _entity("pkg.module_only", "module", None, "a module without inner entities"),
        _entity("pkg.module_only.useful_fn", "function", None, "the useful one"),
    ]
    criteria = [_criterion("X-1:v1:ac1", "useful module work")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)
    ids = [c.entity_id for c in result.candidates]
    assert "pkg.module_only" not in ids
    assert "pkg.module_only.useful_fn" in ids


def test_no_entities_or_no_criteria_returns_empty() -> None:
    assert rank_candidates(criteria=[], entities=[], bm25_only=True).candidates == []
    assert (
        rank_candidates(
            criteria=[],
            entities=[_entity("x.y", "function", None, None)],
            bm25_only=True,
        ).candidates
        == []
    )
    assert (
        rank_candidates(
            criteria=[_criterion("X-1:v1:ac1", "anything")],
            entities=[],
            bm25_only=True,
        ).candidates
        == []
    )


def test_unrelated_entities_are_not_returned_as_candidates() -> None:
    entities = [_entity("pkg.delete_accounts", "function", None, "delete user database")]
    criteria = [_criterion("X-1:v1:ac1", "quasar nebula")]

    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)

    assert result.candidates == []


def test_relevant_entities_are_returned() -> None:
    entities = [
        _entity("pkg.delete_accounts", "function", None, "delete user database"),
        _entity("pkg.calculate_fee", "function", None, "calculate overdue account fee"),
    ]
    criteria = [_criterion("X-1:v1:ac1", "overdue account fee")]

    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)

    assert [candidate.entity_id for candidate in result.candidates] == ["pkg.calculate_fee"]


@pytest.mark.parametrize("entity_count", [1, 5])
def test_corpora_of_five_or_fewer_return_only_entities_with_evidence(entity_count: int) -> None:
    entities = [
        _entity(
            f"pkg.fn{i}",
            "function",
            None,
            "calculate overdue account fee" if i == 0 else "delete user database",
        )
        for i in range(entity_count)
    ]
    criteria = [_criterion("X-1:v1:ac1", "overdue account fee")]

    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)

    assert [candidate.entity_id for candidate in result.candidates] == ["pkg.fn0"]


def test_corpus_larger_than_five_does_not_fill_top_five_with_zero_evidence() -> None:
    entities = [
        _entity(
            f"pkg.fn{i}",
            "function",
            None,
            "calculate overdue account fee" if i == 0 else "delete user database",
        )
        for i in range(8)
    ]
    criteria = [_criterion("X-1:v1:ac1", "overdue account fee")]

    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)

    assert [candidate.entity_id for candidate in result.candidates] == ["pkg.fn0"]


def test_missing_dense_model_falls_back_to_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_model(**_: object) -> tuple[list[list[int | None]], str]:
        raise ImportError("sentence-transformers not installed")

    monkeypatch.setattr("codeatlas.analyze.semantic._dense_rank_matrix", missing_model)
    entities = [
        _entity("pkg.calculate_fee", "function", None, "calculate overdue account fee"),
        _entity("pkg.delete_accounts", "function", None, "delete user database"),
    ]
    criteria = [_criterion("X-1:v1:ac1", "overdue account fee")]

    result = rank_candidates(criteria=criteria, entities=entities)

    assert [candidate.entity_id for candidate in result.candidates] == ["pkg.calculate_fee"]
    assert result.model_id.endswith("@not-installed")


def test_model_id_reflects_bm25_only_mode() -> None:
    entities = [_entity("pkg.a.fn", "function", None, "text")]
    criteria = [_criterion("X-1:v1:ac1", "text")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)
    assert result.model_id.endswith("@bm25-only")


def test_identifiers_are_split_for_matching() -> None:
    """`calculate_late_fee` should match query 'late fee calculation'."""
    entities = [
        _entity("pkg.fees.calculateLateFee", "function", None, None),  # camelCase name
        _entity("pkg.books.add_book", "function", None, None),  # snake_case name
    ]
    criteria = [_criterion("X-1:v1:ac1", "late fee calculation")]
    result = rank_candidates(criteria=criteria, entities=entities, bm25_only=True)
    assert result.candidates[0].entity_id == "pkg.fees.calculateLateFee"
