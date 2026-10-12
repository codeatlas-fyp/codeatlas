"""Run the rules and apply the verdict truth table (spec §7.4; step-4 spec).

The verdict hash is added by `pipeline.evaluate_bundle`, because `evaluate` may import only
`schema` (contract K1) and the hash needs `bundle.hashing`.
"""

from collections.abc import Iterable, Sequence

from codeatlas.evaluate.rules.base import Rule
from codeatlas.schema import CheckResult, EvidenceBundle, TraceLink, VerdictValue


def decide(results: Sequence[CheckResult]) -> VerdictValue:
    """Truth table, first match wins: FAIL, REVIEW, UNKNOWN, PASS."""
    violated = [r for r in results if r.outcome == "VIOLATED"]
    if any(r.severity == "block" for r in violated):
        return "FAIL"
    if any(r.severity == "review" for r in violated):
        return "REVIEW"
    if any(r.required and r.outcome == "INSUFFICIENT_EVIDENCE" for r in results):
        return "UNKNOWN"
    return "PASS"


def run_rules(
    rules: Iterable[Rule], bundle: EvidenceBundle, links: list[TraceLink]
) -> list[CheckResult]:
    """Run each rule once and return the results sorted by case."""
    results: dict[str, CheckResult] = {}
    for rule in rules:
        if rule.case in results:
            raise ValueError(f"more than one rule for {rule.case}")
        try:
            result = rule.check(bundle, links, bundle.policy)
        except Exception as error:
            raise RuntimeError(f"rule {rule.case} failed: {error}") from error
        if result.case != rule.case:
            raise ValueError(f"rule {rule.case} returned a result for {result.case}")
        results[rule.case] = result
    return [results[case] for case in sorted(results)]
