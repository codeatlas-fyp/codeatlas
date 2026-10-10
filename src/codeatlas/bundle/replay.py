"""Replay: recompute a stored verdict from its bundle and prove it is identical (spec §1, §5).

The evaluator is injected because `bundle` may import only `schema` (spec §4).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codeatlas.bundle.hashing import verdict_hash
from codeatlas.bundle.store import bundle_folder, load_bundle, load_verdict
from codeatlas.errors import ReplayMismatchError
from codeatlas.schema import EvidenceBundle, Verdict

Evaluator = Callable[[EvidenceBundle, str], Verdict]


@dataclass(frozen=True)
class ReplayResult:
    bundle_hash: str
    stored_verdict_hash: str
    recomputed_verdict_hash: str
    match: bool


def replay(path: Path, evaluate: Evaluator, *, strict: bool = True) -> ReplayResult:
    """Re-evaluate the stored bundle; with `strict`, a different verdict hash raises."""
    folder = bundle_folder(path)
    bundle = load_bundle(folder)
    stored = load_verdict(folder)
    if stored.bundle_hash != folder.name:
        raise ReplayMismatchError("stored verdict belongs to another bundle")
    # The stored fields must hash to the stored verdict_hash. This catches edits to
    # value, results or ruleset_version in verdict.json (finding A-1).
    self_hash = verdict_hash(
        stored.bundle_hash, stored.ruleset_version, stored.results, stored.value
    )
    if self_hash != stored.verdict_hash:
        raise ReplayMismatchError(
            "verdict.json was altered: its value, results and ruleset_version do not hash "
            f"to its verdict_hash (stored {stored.verdict_hash}, from fields {self_hash})"
        )
    recomputed = evaluate(bundle, folder.name)
    differing = [
        name
        for name in ("value", "results", "ruleset_version")
        if getattr(stored, name) != getattr(recomputed, name)
    ]
    result = ReplayResult(
        bundle_hash=folder.name,
        stored_verdict_hash=stored.verdict_hash,
        recomputed_verdict_hash=recomputed.verdict_hash,
        match=stored.verdict_hash == recomputed.verdict_hash and not differing,
    )
    if strict and not result.match:
        raise ReplayMismatchError(
            f"verdict_hash differs: stored {stored.verdict_hash}, "
            f"recomputed {recomputed.verdict_hash}; fields differing: {differing or 'none'}"
        )
    return result
