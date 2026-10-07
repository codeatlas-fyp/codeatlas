"""Content-addressed bundle store (step-3 spec "Store layout and integrity").

<root>/<bundle_hash>/bundle.json    canonical bytes of the bundle
<root>/<bundle_hash>/verdict.json   canonical bytes of the verdict
"""

import os
from pathlib import Path

from pydantic import ValidationError

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.bundle.hashing import sha256_hex
from codeatlas.errors import EvidenceValidationError, ReplayMismatchError
from codeatlas.schema import EvidenceBundle, Verdict

BUNDLE_FILE = "bundle.json"
VERDICT_FILE = "verdict.json"


def _write_atomically(path: Path, data: bytes) -> None:
    """Write to a temporary file, then rename: a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def bundle_folder(path: Path) -> Path:
    """Accept either a bundle folder or the bundle.json inside it."""
    return path.parent if path.name == BUNDLE_FILE else path


class BundleStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, bundle_hash: str) -> Path:
        return self.root / bundle_hash

    def save(self, bundle: EvidenceBundle) -> str:
        """Store the bundle under its hash and return the hash. Saving twice is harmless."""
        data = canonical_bytes(bundle)
        hash_ = sha256_hex(data)
        folder = self.folder(hash_)
        folder.mkdir(parents=True, exist_ok=True)
        _write_atomically(folder / BUNDLE_FILE, data)
        return hash_

    def save_verdict(self, verdict: Verdict) -> None:
        folder = self.folder(verdict.bundle_hash)
        if not (folder / BUNDLE_FILE).is_file():
            raise EvidenceValidationError(f"no bundle {verdict.bundle_hash} in the store")
        _write_atomically(folder / VERDICT_FILE, canonical_bytes(verdict))


def load_bundle(path: Path) -> EvidenceBundle:
    """Load a stored bundle, refusing anything whose bytes do not prove its identity."""
    folder = bundle_folder(path)
    file = folder / BUNDLE_FILE
    if not file.is_file():
        raise ReplayMismatchError(f"no bundle at {file}")
    raw = file.read_bytes()
    if sha256_hex(raw) != folder.name:
        raise ReplayMismatchError(f"bundle hash does not match its folder name {folder.name}")
    try:
        bundle = EvidenceBundle.model_validate_json(raw)
    except ValidationError as error:
        raise ReplayMismatchError(f"{file} is not a valid bundle: {error}") from error
    if canonical_bytes(bundle) != raw:
        raise ReplayMismatchError(f"{file} is not in canonical form")
    return bundle


def load_verdict(path: Path) -> Verdict:
    file = bundle_folder(path) / VERDICT_FILE
    if not file.is_file():
        raise ReplayMismatchError(f"no stored verdict at {file}")
    try:
        return Verdict.model_validate_json(file.read_bytes())
    except ValidationError as error:
        raise ReplayMismatchError(f"{file} is not a valid verdict: {error}") from error
