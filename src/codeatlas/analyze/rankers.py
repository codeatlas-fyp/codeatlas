"""BM25, MiniLM and CodeT5+ over one identical corpus (CA-02).

All three rankers read the same `Corpus` (same chunks, same text, same pinned commit) and
the same criteria, word for word: the criterion text and its version are never rewritten.
So a difference between rankers comes from the ranker alone.

Scoring:

- each ranker scores every chunk against a criterion;
- an entity's score is its best chunk's score (max), and that chunk is kept as evidence;
- entities are ranked by score, ties broken by entity id so the order is deterministic.

BM25 ranks only entities that share at least one token with the criterion (as before);
dense rankers rank every entity, because a cosine always exists. Whether a score is good
enough to show is the gate's job, not the ranker's.

Each ranker reports a state: `ok`, `unavailable` (its model could not load; it ranks
nothing and the reason is kept) or `fallback` (it ran on a fallback path, such as CodeT5+
through the native T5 loader).

`fuse_candidates` turns the rankings into `SemanticCandidate` rows with reciprocal rank
fusion over the rankers that ran (k = 60, as in step 13).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import numpy as np

from codeatlas.analyze.codet5p import EmbedderUnavailable
from codeatlas.analyze.semantic import RRF_K, _tokenise
from codeatlas.analyze.semantic_corpus import Chunk, Corpus
from codeatlas.schema import Criterion, SemanticCandidate

RANKERS_VERSION = "rankers@1"
RankerName = Literal["bm25", "minilm", "codet5p"]
RankerState = Literal["ok", "unavailable", "fallback"]
BM25_MODEL_ID = "bm25@rank-bm25-okapi"


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    weights: tuple[str, str] | None
    load_mode: str
    load_note: str | None

    def embed(self, texts: Sequence[str]) -> np.ndarray[Any, Any]: ...


@dataclass(frozen=True)
class RankedEntity:
    entity_id: str
    rank: int  # 1 = best
    score: float
    best_chunk_id: str


@dataclass
class RankerOutput:
    name: RankerName
    model_id: str
    state: RankerState
    reason: str | None
    weights: tuple[str, str] | None = None
    seconds: float = 0.0
    rankings: dict[str, list[RankedEntity]] = field(default_factory=dict)  # criterion_id -> list

    def rank_of(self, criterion_id: str, entity_id: str) -> int | None:
        for item in self.rankings.get(criterion_id, []):
            if item.entity_id == entity_id:
                return item.rank
        return None


# ---- BM25 -------------------------------------------------------------------------------------


def rank_bm25(corpus: Corpus, criteria: list[Criterion]) -> RankerOutput:
    chunks = corpus.chunks()
    try:
        from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]
    except ImportError as exc:
        return RankerOutput("bm25", BM25_MODEL_ID, "unavailable", f"missing package: {exc.name}")
    out = RankerOutput("bm25", BM25_MODEL_ID, "ok", None)
    if not chunks:
        out.rankings = {c.criterion_id: [] for c in criteria}
        return out
    tokenised = [_tokenise(chunk.text) for chunk in chunks]
    bm25 = BM25Okapi(tokenised)
    for criterion in criteria:
        query = _tokenise(criterion.text)
        scores = bm25.get_scores(query)
        wanted = set(query)
        evidenced = [i for i, tokens in enumerate(tokenised) if wanted.intersection(tokens)]
        out.rankings[criterion.criterion_id] = _rank_entities(
            chunks, {i: float(scores[i]) for i in evidenced}
        )
    return out


# ---- dense ------------------------------------------------------------------------------------


def rank_dense(
    name: RankerName,
    corpus: Corpus,
    criteria: list[Criterion],
    load: Callable[[], Embedder],
) -> RankerOutput:
    """Embed every chunk and every criterion with one model; rank entities by best cosine."""
    import time

    started = time.perf_counter()
    try:
        embedder = load()
    except EmbedderUnavailable as exc:
        return RankerOutput(name, f"{name}@unavailable", "unavailable", str(exc))
    state: RankerState = "fallback" if embedder.load_note else "ok"
    out = RankerOutput(name, embedder.model_id, state, embedder.load_note, embedder.weights)
    chunks = corpus.chunks()
    if not chunks or not criteria:
        out.rankings = {c.criterion_id: [] for c in criteria}
        out.seconds = time.perf_counter() - started
        return out
    chunk_vecs = np.asarray(embedder.embed([c.text for c in chunks]), dtype=np.float32)
    query_vecs = np.asarray(embedder.embed([c.text for c in criteria]), dtype=np.float32)
    sims = query_vecs @ chunk_vecs.T  # unit vectors, so this is cosine
    for row, criterion in zip(sims, criteria, strict=True):
        out.rankings[criterion.criterion_id] = _rank_entities(
            chunks, {i: float(score) for i, score in enumerate(row)}
        )
    out.seconds = time.perf_counter() - started
    return out


# ---- shared -----------------------------------------------------------------------------------


def _rank_entities(chunks: list[Chunk], chunk_scores: dict[int, float]) -> list[RankedEntity]:
    """Best chunk per entity, then rank entities by that score (ties: entity id)."""
    best: dict[str, tuple[float, str]] = {}
    for i, score in chunk_scores.items():
        if math.isnan(score):
            continue
        chunk = chunks[i]
        current = best.get(chunk.entity_id)
        if current is None or score > current[0]:
            best[chunk.entity_id] = (score, chunk.chunk_id)
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return [
        RankedEntity(entity_id=entity_id, rank=rank, score=score, best_chunk_id=chunk_id)
        for rank, (entity_id, (score, chunk_id)) in enumerate(ordered, start=1)
    ]


def fuse_candidates(
    criteria: list[Criterion],
    outputs: list[RankerOutput],
    *,
    top_k: int,
) -> list[SemanticCandidate]:
    """Reciprocal rank fusion over the rankers that ran; top-k per criterion.

    `bm25_rank`, `dense_rank` (MiniLM) and `codet5_rank` keep each ranker's own rank, so a
    reader can see which ranker put an entity there. `model_id` names the fused rankers.
    """
    used = [o for o in outputs if o.state != "unavailable"]
    by_name = {o.name: o for o in used}
    fused_id = "+".join(o.model_id for o in sorted(used, key=lambda o: o.name)) or "none"
    candidates: list[SemanticCandidate] = []
    for criterion in criteria:
        scores: dict[str, float] = {}
        for output in used:
            for item in output.rankings.get(criterion.criterion_id, []):
                scores[item.entity_id] = scores.get(item.entity_id, 0.0) + 1.0 / (RRF_K + item.rank)
        ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        for fused_rank, (entity_id, score) in enumerate(ordered, start=1):
            cid = criterion.criterion_id
            candidates.append(
                SemanticCandidate(
                    criterion_id=cid,
                    entity_id=entity_id,
                    bm25_rank=_rank(by_name.get("bm25"), cid, entity_id),
                    dense_rank=_rank(by_name.get("minilm"), cid, entity_id),
                    codet5_rank=_rank(by_name.get("codet5p"), cid, entity_id),
                    fused_rank=fused_rank,
                    fused_score=round(score, 4),
                    model_id=fused_id,
                )
            )
    return candidates


def _rank(output: RankerOutput | None, criterion_id: str, entity_id: str) -> int | None:
    return output.rank_of(criterion_id, entity_id) if output is not None else None
