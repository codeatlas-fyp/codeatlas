"""semantic-run-v1 fields (CA-02): old bundles keep their hash, new fields are hashed."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.bundle.hashing import bundle_hash
from codeatlas.bundle.store import BundleStore, load_bundle
from codeatlas.schema import SemanticCandidate
from tests.samples import BUNDLE, CANDIDATE, SEMANTIC_RUN

SAMPLE_HASH = "cb973272d317b0a5e4d2f5b2198bf53740bac9ab2c93d07d581a51ff40ea4f66"


def test_old_candidate_and_bundle_bytes_are_unchanged() -> None:
    assert CANDIDATE.codet5_rank is None and CANDIDATE.gate is None
    assert b"codet5_rank" not in canonical_bytes(CANDIDATE)
    assert b"semantic_run" not in canonical_bytes(BUNDLE)
    assert bundle_hash(BUNDLE) == SAMPLE_HASH


def test_new_fields_are_hashed_when_set() -> None:
    gated = CANDIDATE.model_copy(update={"gate": "accept", "codet5_rank": 2})
    assert b'"gate":"accept"' in canonical_bytes(gated)
    with_run = BUNDLE.model_copy(update={"candidates": [gated], "semantic_run": SEMANTIC_RUN})
    assert bundle_hash(with_run) != SAMPLE_HASH


def test_bundle_with_semantic_run_round_trips_through_the_store(tmp_path: Path) -> None:
    with_run = BUNDLE.model_copy(update={"semantic_run": SEMANTIC_RUN})
    store = BundleStore(tmp_path / "store")
    hash_ = store.save(with_run)
    loaded = load_bundle(store.root / hash_)
    assert loaded.semantic_run == SEMANTIC_RUN
    assert canonical_bytes(loaded) == canonical_bytes(with_run)


def test_unknown_gate_value_is_refused() -> None:
    with pytest.raises(ValidationError):
        SemanticCandidate.model_validate(CANDIDATE.model_dump() | {"gate": "maybe"})
