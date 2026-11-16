"""Pinned model registry for the semantic rankers (CA-02).

Every embedding model CodeAtlas may load is listed here with the exact Hugging Face revision
(commit SHA) to load. Nothing else is loaded: an adapter asks the registry for a spec, loads
that revision, and reports the SHA-256 of the weights file it actually read, so a bundle can
prove which bytes produced its scores (`EvidenceBundle.model_file_hashes`).

The registry is a plain, versioned table in the repository, not a server: changing a model or a
revision is a reviewed commit, and `REGISTRY_VERSION` goes up with it.

Revisions were read with `huggingface_hub.HfApi().model_info(...).sha` on 17 Nov 2026.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REGISTRY_VERSION = "models@1"

ModelKey = Literal["minilm", "codet5p"]
Interface = Literal["sentence-transformers", "transformers-remote-code"]

# Weight files an adapter may hash, in order of preference.
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


@dataclass(frozen=True)
class ModelSpec:
    key: ModelKey
    repo_id: str
    revision: str  # full 40-character Hugging Face commit SHA
    interface: Interface
    dim: int  # embedding size
    max_tokens: int  # longest input the model accepts; longer text is split, never cut
    note: str

    @property
    def model_id(self) -> str:
        """The string stored in bundles: `<repo_id>@<revision>`."""
        return f"{self.repo_id}@{self.revision}"


REGISTRY: dict[ModelKey, ModelSpec] = {
    "minilm": ModelSpec(
        key="minilm",
        repo_id="sentence-transformers/all-MiniLM-L6-v2",
        revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        interface="sentence-transformers",
        dim=384,
        max_tokens=256,
        note="General English sentence model; the baseline dense ranker since step 13.",
    ),
    "codet5p": ModelSpec(
        key="codet5p",
        repo_id="Salesforce/codet5p-110m-embedding",
        revision="d9f3a534af4252f04b10cd9f78e05037542d10f6",
        interface="transformers-remote-code",
        dim=256,
        max_tokens=512,
        note="Code-aware encoder; loaded through AutoModel with its documented remote code.",
    ),
}


def spec(key: ModelKey) -> ModelSpec:
    return REGISTRY[key]


def weights_sha256(snapshot_dir: Path) -> tuple[str, str] | None:
    """(file name, SHA-256) of the first weights file found in a model snapshot folder."""
    for name in WEIGHT_FILES:
        path = snapshot_dir / name
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            return name, digest.hexdigest()
    return None
