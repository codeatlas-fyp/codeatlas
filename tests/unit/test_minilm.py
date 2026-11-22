"""MiniLM on the shared window logic (CA-02). Fake model: no network."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from codeatlas.analyze.codet5p import CodeT5pEmbedder, EmbedderUnavailable
from codeatlas.analyze.minilm import _SentenceTransformerBackend, load_minilm
from codeatlas.analyze.model_registry import spec


class FakeTokenizer:
    def __call__(self, text: str, add_special_tokens: bool) -> dict[str, list[int]]:
        assert add_special_tokens is False
        return {"input_ids": [ord(c) for c in text]}

    def decode(self, ids: list[int]) -> str:
        return "".join(chr(i) for i in ids)


class FakeSentenceModel:
    def __init__(self) -> None:
        self.tokenizer = FakeTokenizer()
        self.encoded: list[str] = []

    def encode(self, texts: list[str], **kwargs: Any) -> np.ndarray[Any, Any]:
        assert kwargs["normalize_embeddings"] is True
        self.encoded.extend(texts)
        return np.array([[len(t), 1.0] for t in texts], dtype=np.float32)


def test_long_text_is_split_into_windows_of_whole_text() -> None:
    from dataclasses import replace

    model = FakeSentenceModel()
    embedder = CodeT5pEmbedder(
        backend=_SentenceTransformerBackend(model),
        model_spec=replace(spec("minilm"), dim=2, max_tokens=7),
    )
    vecs = embedder.embed(["abcdefghijk"])  # window 5 -> "abcde", "fghij", "k"
    assert model.encoded == ["abcde", "fghij", "k"]
    assert vecs.shape == (1, 2)
    assert np.isclose(np.linalg.norm(vecs[0]), 1.0)


def test_minilm_model_id_is_pinned() -> None:
    embedder = CodeT5pEmbedder(
        backend=_SentenceTransformerBackend(FakeSentenceModel()), model_spec=spec("minilm")
    )
    assert embedder.model_id.startswith("sentence-transformers/all-MiniLM-L6-v2@1110a243")


def test_model_not_in_local_cache_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(EmbedderUnavailable):
        load_minilm(cache_dir=tmp_path, local_files_only=True)
