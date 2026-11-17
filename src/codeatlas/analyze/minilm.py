"""Local MiniLM embedder on the same terms as CodeT5+ (CA-02).

MiniLM stays the baseline dense ranker, but it is now loaded at the pinned revision from
`model_registry`, its weights are hashed, its vectors are cached, and it no longer truncates:
SentenceTransformer silently cuts input at `max_seq_length` (256 tokens for this model), so
long text is split into token windows by the shared `CodeT5pEmbedder` window logic instead.
That way BM25, MiniLM and CodeT5+ all see exactly the same text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from codeatlas.analyze.codet5p import CodeT5pEmbedder, EmbedderUnavailable
from codeatlas.analyze.embedding_cache import EmbeddingCache
from codeatlas.analyze.model_registry import spec, weights_sha256


class _SentenceTransformerBackend:
    """Token windows in, unit vectors out, through the model's own tokenizer."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.tokenizer = model.tokenizer

    def token_ids(self, text: str) -> list[int]:
        return list(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def embed_ids(self, batch: list[list[int]]) -> np.ndarray[Any, Any]:
        texts = [self.tokenizer.decode(ids) for ids in batch]
        vecs = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vecs, dtype=np.float32)


def load_minilm(
    *,
    cache_dir: Path | None = None,
    embedding_cache_dir: Path | None = None,
    local_files_only: bool = False,
) -> CodeT5pEmbedder:
    """Load the pinned MiniLM revision; raise EmbedderUnavailable with a reason on failure."""
    model_spec = spec("minilm")
    try:
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbedderUnavailable(f"missing package: {exc.name}") from exc
    try:
        snapshot = Path(
            snapshot_download(
                model_spec.repo_id,
                revision=model_spec.revision,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
            )
        )
        model = SentenceTransformer(str(snapshot), device="cpu")
    except Exception as exc:  # any load failure means "unavailable", and says why
        raise EmbedderUnavailable(f"{type(exc).__name__}: {exc}") from exc
    cache = (
        EmbeddingCache(embedding_cache_dir, model_spec.model_id)
        if embedding_cache_dir is not None
        else None
    )
    embedder = CodeT5pEmbedder(
        backend=_SentenceTransformerBackend(model),
        model_spec=model_spec,
        cache=cache,
        weights=weights_sha256(snapshot),
    )
    embedder.load_mode = "sentence-transformers"
    return embedder
