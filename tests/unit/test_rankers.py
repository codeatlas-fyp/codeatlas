"""BM25, MiniLM and CodeT5+ on one identical corpus (CA-02). Fake embedders: no network."""

from collections.abc import Sequence
from typing import Any

import numpy as np

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.codet5p import EmbedderUnavailable
from codeatlas.analyze.rankers import (
    BM25_MODEL_ID,
    fuse_candidates,
    rank_bm25,
    rank_dense,
)
from codeatlas.analyze.semantic_corpus import build_corpus
from codeatlas.schema import Criterion

SOURCE = b'''
def calculate_late_fee(days_overdue: int) -> int:
    """Late fee for an overdue book."""
    return days_overdue * 2


def add_book(catalog, book) -> None:
    """Add a book to the catalog."""
    catalog.append(book)


def parse_config(path):
    return open(path).read()
'''
VOCAB = ["fee", "late", "overdue", "book", "catalog", "add", "config", "parse", "path"]


class BagOfWords:
    """A fake embedder: counts of a few words, normalised. Records every text it saw."""

    model_id = "fake/bow@1"
    weights = ("model.safetensors", "0" * 64)

    def __init__(self, load_note: str | None = None) -> None:
        self.load_mode = "fake"
        self.load_note = load_note
        self.seen: list[str] = []

    def embed(self, texts: Sequence[str]) -> np.ndarray[Any, Any]:
        self.seen.extend(texts)
        rows = []
        for text in texts:
            lower = text.lower()
            vec = np.array([lower.count(w) for w in VOCAB], dtype=np.float32) + 1e-3
            rows.append(vec / np.linalg.norm(vec))
        return np.array(rows)


def _corpus():  # type: ignore[no-untyped-def]
    entities = extract_entities(file_path="lib/fees.py", source=SOURCE)
    return build_corpus(
        entities=entities, file_sources={"lib/fees.py": SOURCE}, pinned_sha="a" * 40
    )


CRITERIA = [
    Criterion(
        criterion_id="SBX-4:v2:ac1",
        key="SBX-4",
        version_no=2,
        text="Charge a late fee when a book is overdue.",
        derived_from_description=False,
    )
]


def test_bm25_ranks_the_fee_function_first_and_skips_no_overlap() -> None:
    out = rank_bm25(_corpus(), CRITERIA)
    ranking = out.rankings["SBX-4:v2:ac1"]
    assert (out.state, out.model_id) == ("ok", BM25_MODEL_ID)
    assert ranking[0].entity_id == "lib.fees.calculate_late_fee"
    assert "lib.fees.parse_config" not in [r.entity_id for r in ranking]


def test_dense_ranker_reads_the_same_chunks_and_the_criterion_verbatim() -> None:
    corpus = _corpus()
    embedder = BagOfWords()
    out = rank_dense("codet5p", corpus, CRITERIA, lambda: embedder)
    assert embedder.seen == [c.text for c in corpus.chunks()] + [CRITERIA[0].text]
    ranking = out.rankings["SBX-4:v2:ac1"]
    assert [r.rank for r in ranking] == [1, 2, 3]  # dense ranks every entity
    assert ranking[0].entity_id == "lib.fees.calculate_late_fee"
    assert ranking[0].best_chunk_id == "lib.fees.calculate_late_fee#0"
    assert (out.state, out.weights) == ("ok", BagOfWords.weights)


def test_unavailable_model_ranks_nothing_and_keeps_the_reason() -> None:
    def broken() -> BagOfWords:
        raise EmbedderUnavailable("missing package: torch")

    out = rank_dense("minilm", _corpus(), CRITERIA, broken)
    assert (out.state, out.reason, out.rankings) == (
        "unavailable",
        "missing package: torch",
        {},
    )
    assert out.model_id == "minilm@unavailable"


def test_fallback_load_is_reported_as_fallback() -> None:
    out = rank_dense("codet5p", _corpus(), CRITERIA, lambda: BagOfWords("native-t5 used"))
    assert (out.state, out.reason) == ("fallback", "native-t5 used")


def test_ties_are_broken_by_entity_id() -> None:
    class Flat(BagOfWords):
        def embed(self, texts: Sequence[str]) -> np.ndarray[Any, Any]:
            return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)

    ranking = rank_dense("minilm", _corpus(), CRITERIA, Flat).rankings["SBX-4:v2:ac1"]
    assert [r.entity_id for r in ranking] == sorted(r.entity_id for r in ranking)


def test_fusion_keeps_each_rankers_rank_and_skips_unavailable() -> None:
    corpus = _corpus()
    bm25 = rank_bm25(corpus, CRITERIA)
    codet5p = rank_dense("codet5p", corpus, CRITERIA, BagOfWords)
    minilm = rank_dense("minilm", corpus, CRITERIA, _raise_unavailable)
    candidates = fuse_candidates(CRITERIA, [bm25, minilm, codet5p], top_k=2)
    assert [c.fused_rank for c in candidates] == [1, 2]
    top = candidates[0]
    assert top.entity_id == "lib.fees.calculate_late_fee"
    assert (top.bm25_rank, top.dense_rank, top.codet5_rank) == (1, None, 1)
    assert top.fused_score == round(2 / 61, 4)
    assert top.model_id == f"{BM25_MODEL_ID}+fake/bow@1"


def test_fusion_with_nothing_available_gives_no_candidates() -> None:
    minilm = rank_dense("minilm", _corpus(), CRITERIA, _raise_unavailable)
    assert fuse_candidates(CRITERIA, [minilm], top_k=5) == []


def test_empty_corpus_gives_empty_rankings() -> None:
    empty = build_corpus(entities=[], file_sources={}, pinned_sha="a" * 40)
    assert rank_bm25(empty, CRITERIA).rankings == {"SBX-4:v2:ac1": []}
    assert rank_dense("codet5p", empty, CRITERIA, BagOfWords).rankings == {"SBX-4:v2:ac1": []}


def _raise_unavailable() -> BagOfWords:
    raise EmbedderUnavailable("not installed")
