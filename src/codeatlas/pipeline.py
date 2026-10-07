"""Pipeline orchestration (spec §5). Step 4 adds the offline half: stages 12 and 13.

`evaluate_bundle` is also the evaluator injected into `bundle.replay`.
"""

from collections.abc import Iterable

from codeatlas.bundle.hashing import verdict_hash
from codeatlas.errors import EvidenceValidationError
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.base import Rule
from codeatlas.evaluate.verdict import decide, run_rules
from codeatlas.schema import EvidenceBundle, TraceLink, Verdict


def evaluate_bundle(
    bundle: EvidenceBundle,
    bundle_hash: str,
    rules: Iterable[Rule] | None = None,
    links: list[TraceLink] | None = None,
) -> Verdict:
    """Run the rules over a frozen bundle and seal the verdict with its hash.

    A rule that raises is a bug: no verdict is produced and the error has exit code 4.
    """
    chosen = tuple(default_rules() if rules is None else rules)
    try:
        results = run_rules(chosen, bundle, links or [])
    except Exception as error:
        raise EvidenceValidationError(f"rule evaluation failed: {error}") from error
    value = decide(results)
    version = ruleset_version(chosen)
    return Verdict(
        value=value,
        results=results,
        ruleset_version=version,
        bundle_hash=bundle_hash,
        verdict_hash=verdict_hash(bundle_hash, version, results, value),
    )
