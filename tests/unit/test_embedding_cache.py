"""On-disk embedding cache (CA-02)."""

from pathlib import Path

import numpy as np

from codeatlas.analyze.embedding_cache import EmbeddingCache


def test_round_trip_and_hit_counts(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path, "org/model@abc")
    assert cache.get("def f(): pass") is None
    cache.put("def f(): pass", np.array([0.6, 0.8], dtype=np.float32))
    again = EmbeddingCache(tmp_path, "org/model@abc").get("def f(): pass")
    assert again is not None and again.tolist() == [np.float32(0.6), np.float32(0.8)]
    assert (cache.hits, cache.misses) == (0, 1)


def test_other_revision_or_text_is_a_miss(tmp_path: Path) -> None:
    EmbeddingCache(tmp_path, "org/model@abc").put("x", np.ones(2, dtype=np.float32))
    assert EmbeddingCache(tmp_path, "org/model@def").get("x") is None
    assert EmbeddingCache(tmp_path, "org/model@abc").get("x ") is None


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path, "org/model@abc")
    cache.put("x", np.ones(2, dtype=np.float32))
    assert [p.suffix for p in tmp_path.rglob("*") if p.is_file()] == [".npy"]
