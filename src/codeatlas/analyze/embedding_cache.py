"""On-disk cache of text embeddings, keyed by model id and exact text (CA-02).

Embedding a whole repository is the slow part of a semantic run. The same chunk text under the
same pinned model always gives the same vector, so it is computed once and read back after.
The key is SHA-256 over `model_id`, a NUL byte and the UTF-8 text: a new revision or a single
changed character is a cache miss, never a stale hit.

Layout: `<root>/<model-slug>/<key[:2]>/<key>.npy`, written to a temp file and then renamed, so
a crash never leaves half a vector behind. This is a plain folder of files, not a vector
database: it is never searched, only looked up by key.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import numpy.typing as npt

Vector = npt.NDArray[np.float32]


class EmbeddingCache:
    def __init__(self, root: Path, model_id: str) -> None:
        self.model_id = model_id
        self.folder = root / re.sub(r"[^A-Za-z0-9._-]+", "_", model_id)
        self.hits = 0
        self.misses = 0

    def key(self, text: str) -> str:
        return hashlib.sha256(self.model_id.encode() + b"\0" + text.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.npy"

    def get(self, text: str) -> Vector | None:
        path = self._path(self.key(text))
        if not path.is_file():
            self.misses += 1
            return None
        self.hits += 1
        vector: Vector = np.load(path).astype(np.float32)
        return vector

    def put(self, text: str, vector: Vector) -> None:
        path = self._path(self.key(text))
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                np.save(handle, np.asarray(vector, dtype=np.float32))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
