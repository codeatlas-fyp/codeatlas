"""Bundle and verdict hashes (spec §7.4)."""

import hashlib

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.schema import CheckResult, EvidenceBundle, VerdictValue


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle_hash(bundle: EvidenceBundle) -> str:
    """SHA-256 of the bundle's canonical bytes. The bundle holds no hash of itself (step-2 S3)."""
    return sha256_hex(canonical_bytes(bundle))


def verdict_hash(
    bundle_hash: str, ruleset_version: str, results: list[CheckResult], value: VerdictValue
) -> str:
    """SHA-256(bundle_hash ‖ ruleset_version ‖ canonical(results, value)), ‖ = concatenation."""
    body = canonical_bytes({"results": results, "value": value})
    return sha256_hex(bundle_hash.encode() + ruleset_version.encode() + body)
