"""One semantic run, end to end (CA-02, semantic-run-v1).

corpus (bodies, spans) -> BM25 + MiniLM + CodeT5+ on that corpus -> fused candidates ->
accept/review/abstain gate -> a `SemanticRunSummary` frozen into the bundle.

The run also returns what the bundle needs for replay and audit: `model_ids` and
`model_file_hashes` (model id -> SHA-256 of the weights file that was really loaded), and
the full ranker outputs, which the semantic-run-v1 export writes out for evaluation.

`bm25_only=True` (offline runs and CI) records both dense rankers as unavailable with the
reason "disabled (bm25_only)", so a bundle always says which rankers did not run and why.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from codeatlas.analyze import rankers
from codeatlas.analyze.codet5p import EmbedderUnavailable, load_codet5p
from codeatlas.analyze.minilm import load_minilm
from codeatlas.analyze.rankers import Embedder, RankerOutput
from codeatlas.analyze.semantic_corpus import Corpus, build_corpus
from codeatlas.analyze.semantic_gate import (
    GateCalibration,
    apply_gate,
    decide,
    load_calibration,
)
from codeatlas.schema import (
    CodeEntity,
    Criterion,
    CriterionGate,
    RankerRun,
    SemanticCandidate,
    SemanticRunSummary,
)

SCHEMA_ID: Final = "semantic-run-v1"
EXTRACTOR_VERSION = "semantic-run@1"

Loader = Callable[[], Embedder]


@dataclass(frozen=True)
class SemanticRunResult:
    corpus: Corpus
    outputs: list[RankerOutput]
    candidates: list[SemanticCandidate]
    gates: list[CriterionGate]
    summary: SemanticRunSummary
    model_ids: list[str]
    model_file_hashes: dict[str, str]


def default_loaders(cache_root: Path | None = None) -> dict[str, Loader]:
    embeddings = cache_root / "embeddings" if cache_root is not None else None
    return {
        "minilm": lambda: load_minilm(embedding_cache_dir=embeddings),
        "codet5p": lambda: load_codet5p(embedding_cache_dir=embeddings),
    }


def run_semantic(
    *,
    criteria: list[Criterion],
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
    pinned_sha: str,
    top_k: int,
    bm25_only: bool = False,
    calibration: GateCalibration | None = None,
    loaders: dict[str, Loader] | None = None,
    cache_root: Path | None = None,
) -> SemanticRunResult:
    """`cache_root` is where the default loaders keep embedding vectors between runs (a
    folder `embeddings/` inside it); None keeps no cache. Ignored when `loaders` is given."""
    corpus = build_corpus(entities=entities, file_sources=file_sources, pinned_sha=pinned_sha)
    calibration = calibration or load_calibration()
    loaders = loaders if loaders is not None else default_loaders(cache_root)

    outputs = [rankers.rank_bm25(corpus, criteria)]
    for name in ("minilm", "codet5p"):
        load = loaders.get(name)
        if bm25_only or load is None:
            why = "disabled (bm25_only)" if bm25_only else "no loader configured"
            outputs.append(RankerOutput(name, f"{name}@unavailable", "unavailable", why))
            continue
        outputs.append(rankers.rank_dense(name, corpus, criteria, load))

    candidates = rankers.fuse_candidates(criteria, outputs, top_k=top_k)
    gates = decide([c.criterion_id for c in criteria], outputs, calibration)
    candidates = apply_gate(candidates, gates)

    gate_name = next(
        (
            n
            for n in calibration.order
            if any(o.name == n and o.state != "unavailable" for o in outputs)
        ),
        None,
    )
    limits = calibration.thresholds.get(gate_name) if gate_name else None
    summary = SemanticRunSummary(
        schema_id=SCHEMA_ID,
        pinned_sha=pinned_sha,
        corpus_hash=corpus.corpus_hash,
        chunker_version=corpus.chunker_version,
        rankers=[
            RankerRun(name=o.name, model_id=o.model_id, state=o.state, reason=o.reason)
            for o in outputs
        ],
        calibration_id=calibration.calibration_id
        + (" (provisional)" if calibration.provisional else ""),
        accept_threshold=_finite(limits.accept) if limits else None,
        review_threshold=_finite(limits.review) if limits else None,
        gates=gates,
    )
    ran = [o for o in outputs if o.state != "unavailable"]
    return SemanticRunResult(
        corpus=corpus,
        outputs=outputs,
        candidates=candidates,
        gates=gates,
        summary=summary,
        model_ids=sorted(o.model_id for o in ran) if candidates else [],
        model_file_hashes={o.model_id: o.weights[1] for o in ran if o.weights is not None},
    )


def _finite(value: float) -> float | None:
    """A threshold as stored in the bundle: +inf ("never") becomes None, as in the
    calibration file, so the summary is strict JSON."""
    return None if math.isinf(value) else value


__all__ = [
    "EXTRACTOR_VERSION",
    "SCHEMA_ID",
    "EmbedderUnavailable",
    "SemanticRunResult",
    "default_loaders",
    "run_semantic",
]
