"""Semantic candidate generation (spec §6 `analyze.semantic`, step-13).

For each acceptance criterion, rank code entities by relevance and keep the
top-K. Two independent rankers are combined with reciprocal rank fusion:

1. **BM25** — classical lexical scorer over tokenised entity text (name,
   signature, docstring). Fast, deterministic, no model download.
2. **Dense embeddings** — `sentence-transformers/all-MiniLM-L6-v2` (pinned by
   name; the actual revision is captured at run time into
   `bundle.model_ids`). Cosine similarity between criterion embedding and
   each entity's embedding.

The two rank lists are fused with reciprocal rank fusion:

    rrf_score(entity) = sum_over_rankers( 1 / (k + rank_in_that_ranker) )

with `k = 60` (the standard value from Cormack et al. 2009). We keep the
top-5 entities per criterion as `SemanticCandidate` evidence.

Pure function from the caller's perspective. The model is loaded on first
use and cached by `sentence-transformers`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from codeatlas.schema import CodeEntity, Criterion, SemanticCandidate

if TYPE_CHECKING:
    import numpy as np

EXTRACTOR_VERSION = "semantic@0.1.0"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
RRF_K = 60
_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


@dataclass(frozen=True)
class SemanticResult:
    """Result of one semantic run: candidates + the model_id captured at run time.

    `model_id` is the string that lands in bundle.model_ids — format is
    `<name>@<revision>` where revision is the Hugging Face commit SHA
    discovered at load time (or 'unknown' if the model shipped without one).
    """

    candidates: list[SemanticCandidate]
    model_id: str


def rank_candidates(
    *,
    criteria: list[Criterion],
    entities: list[CodeEntity],
    top_k: int = 5,
    model_name: str = DEFAULT_MODEL,
    bm25_only: bool = False,
) -> SemanticResult:
    """Produce top-k SemanticCandidate per criterion, fused BM25 + dense.

    `bm25_only=True` skips the embedding model entirely — useful for CI runs
    without network or when the model isn't installed.
    """
    rankable = [e for e in entities if e.kind in ("function", "method", "class")]
    if not criteria or not rankable:
        return SemanticResult(candidates=[], model_id=f"{model_name}@skipped")

    corpus = [_entity_text(e) for e in rankable]
    tokenised = [_tokenise(text) for text in corpus]

    bm25_ranks = _bm25_rank_matrix(
        tokenised_corpus=tokenised, queries=[c.text for c in criteria],
    )

    model_id = f"{model_name}@bm25-only"
    dense_ranks: list[list[int | None]] = [
        [None] * len(rankable) for _ in criteria
    ]
    if not bm25_only:
        try:
            dense_ranks, model_id = _dense_rank_matrix(
                corpus=corpus, queries=[c.text for c in criteria], model_name=model_name,
            )
        except ImportError:
            # sentence-transformers not installed; fall back to BM25-only.
            dense_ranks = [[None] * len(rankable) for _ in criteria]
            model_id = f"{model_name}@not-installed"

    all_candidates: list[SemanticCandidate] = []
    for ci, criterion in enumerate(criteria):
        bm25_for_c = bm25_ranks[ci]
        dense_for_c = dense_ranks[ci]
        fused = _reciprocal_rank_fusion(bm25_for_c, dense_for_c)
        ordered = sorted(range(len(rankable)), key=lambda idx: -fused[idx])[:top_k]
        for fused_rank, entity_idx in enumerate(ordered, start=1):
            all_candidates.append(
                SemanticCandidate(
                    criterion_id=criterion.criterion_id,
                    entity_id=rankable[entity_idx].entity_id,
                    bm25_rank=bm25_for_c[entity_idx],
                    dense_rank=dense_for_c[entity_idx],
                    fused_rank=fused_rank,
                    fused_score=round(fused[entity_idx], 4),
                    model_id=model_id,
                )
            )
    return SemanticResult(candidates=all_candidates, model_id=model_id)


# ---- BM25 ----------------------------------------------------------------

def _bm25_rank_matrix(
    *, tokenised_corpus: list[list[str]], queries: list[str],
) -> list[list[int | None]]:
    """Return per-query rank of each doc (1 = best). None means doc had zero score."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        # Degrade: no BM25 → every doc tied at rank None.
        return [[None] * len(tokenised_corpus) for _ in queries]

    bm25 = BM25Okapi(tokenised_corpus)
    out: list[list[int | None]] = []
    for query in queries:
        q_tokens = _tokenise(query)
        scores = bm25.get_scores(q_tokens)
        ordered = sorted(range(len(scores)), key=lambda idx: -scores[idx])
        ranks: list[int | None] = [None] * len(scores)
        for rank, idx in enumerate(ordered, start=1):
            if scores[idx] > 0:
                ranks[idx] = rank
        out.append(ranks)
    return out


# ---- Dense embeddings ----------------------------------------------------

def _dense_rank_matrix(
    *, corpus: list[str], queries: list[str], model_name: str,
) -> tuple[list[list[int | None]], str]:
    """Load the sentence-transformer, embed, rank by cosine. Returns (ranks, model_id)."""
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(model_name)
    revision = _model_revision(model)
    model_id = f"{model_name}@{revision}"

    corpus_vecs = model.encode(corpus, normalize_embeddings=True)
    query_vecs = model.encode(queries, normalize_embeddings=True)

    # Cosine sim is just dot product when both sides are unit-normalised.
    sims = np.asarray(query_vecs) @ np.asarray(corpus_vecs).T

    out: list[list[int | None]] = []
    for row in sims:
        ordered = sorted(range(len(row)), key=lambda idx: -row[idx])
        ranks: list[int | None] = [None] * len(row)
        for rank, idx in enumerate(ordered, start=1):
            if row[idx] > 0:
                ranks[idx] = rank
        out.append(ranks)
    return out, model_id


def _model_revision(model: object) -> str:
    """Pull the Hugging Face commit SHA from the loaded model, or 'unknown'."""
    try:
        card = getattr(model, "_model_card_vars", None) or {}
        if "model_sha" in card:
            return str(card["model_sha"])
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is not None:
            # sentence-transformers stores the HF snapshot path; last segment
            # is the revision for snapshots that come from a specific commit.
            name_or_path = getattr(tokenizer, "name_or_path", "")
            m = re.search(r"snapshots/([a-f0-9]{7,})", str(name_or_path))
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


# ---- Reciprocal rank fusion ---------------------------------------------

def _reciprocal_rank_fusion(
    bm25_ranks: list[int | None], dense_ranks: list[int | None],
) -> list[float]:
    """Combine two rank lists into one fused score per doc.

    Formula: `score(doc) = sum_over_rankers(1 / (k + rank))`. Unranked docs
    (rank is None) contribute zero from that ranker.
    """
    n = len(bm25_ranks)
    scores = [0.0] * n
    for i in range(n):
        if bm25_ranks[i] is not None:
            scores[i] += 1.0 / (RRF_K + bm25_ranks[i])
        if dense_ranks[i] is not None:
            scores[i] += 1.0 / (RRF_K + dense_ranks[i])
    return scores


# ---- Tokenisation --------------------------------------------------------

def _entity_text(entity: CodeEntity) -> str:
    """Build the text we rank against: name, signature, docstring."""
    name_tokens = _split_identifier(entity.entity_id.rsplit(".", 1)[-1])
    parts = [name_tokens]
    if entity.signature:
        parts.append(entity.signature)
    if entity.docstring:
        parts.append(entity.docstring)
    return " ".join(parts)


def _tokenise(text: str) -> list[str]:
    """Lowercased word tokens, plus identifier-split camelCase/snake_case names."""
    raw_tokens = _TOKEN_RE.findall(text or "")
    out: list[str] = []
    for token in raw_tokens:
        parts = _split_identifier(token)
        out.extend(parts.lower().split())
    return out


def _split_identifier(name: str) -> str:
    """`calculate_late_fee` → `calculate late fee`; `calcLateFee` → `calc Late Fee`."""
    # Split snake_case:
    s = name.replace("_", " ")
    # Split camelCase: insert space before any uppercase that follows a lowercase.
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    # And before uppercase that precedes another uppercase+lowercase (e.g. HTTPServer → HTTP Server).
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    return s
