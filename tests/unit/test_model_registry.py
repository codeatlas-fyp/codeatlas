"""Pinned model registry (CA-02)."""

import hashlib
import re
from pathlib import Path

from codeatlas.analyze.model_registry import REGISTRY, spec, weights_sha256


def test_every_model_is_pinned_to_a_full_commit_sha() -> None:
    for model in REGISTRY.values():
        assert re.fullmatch(r"[0-9a-f]{40}", model.revision), model.key
        assert model.model_id == f"{model.repo_id}@{model.revision}"


def test_codet5p_uses_its_documented_interface_and_size() -> None:
    codet5p = spec("codet5p")
    assert codet5p.repo_id == "Salesforce/codet5p-110m-embedding"
    assert codet5p.interface == "transformers-remote-code"
    assert (codet5p.dim, codet5p.max_tokens) == (256, 512)


def test_weights_hash_prefers_safetensors(tmp_path: Path) -> None:
    (tmp_path / "pytorch_model.bin").write_bytes(b"bin")
    (tmp_path / "model.safetensors").write_bytes(b"safe")
    assert weights_sha256(tmp_path) == (
        "model.safetensors",
        hashlib.sha256(b"safe").hexdigest(),
    )


def test_weights_hash_is_none_without_weights(tmp_path: Path) -> None:
    assert weights_sha256(tmp_path) is None
