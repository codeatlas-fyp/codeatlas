"""Compare persisted evaluations without interpreting missing evidence as resolution."""

from typing import Any

from codeatlas.api.cases import Case
from codeatlas.bundle.hashing import effective_policy_hash
from codeatlas.schema import CheckResult


def _finding_change(left: CheckResult | None, right: CheckResult | None) -> str:
    if left is None or right is None:
        return "not_comparable"
    if left.outcome == "INSUFFICIENT_EVIDENCE" or right.outcome == "INSUFFICIENT_EVIDENCE":
        return "not_comparable"
    if left.outcome == "VIOLATED" and right.outcome == "SATISFIED":
        return "resolved"
    if left.outcome == "SATISFIED" and right.outcome == "VIOLATED":
        return "new"
    if left.outcome == right.outcome == "VIOLATED" and left.severity != right.severity:
        return "severity_changed"
    if (
        left.outcome == right.outcome
        and left.severity == right.severity
        and left.required == right.required
        and left.message == right.message
        and sorted(left.evidence_ids) == sorted(right.evidence_ids)
    ):
        return "unchanged"
    return "changed"


def compare_cases(left: Case, right: Case, left_id: str, right_id: str) -> dict[str, Any]:
    a, b = left.bundle, right.bundle
    reasons: list[str] = []
    same_target = (a.change.repo, a.change.pr_number) == (b.change.repo, b.change.pr_number)
    if not same_target:
        reasons.append("Different repository or pull request.")
    if (a.change.base_sha, a.change.head_sha) != (b.change.base_sha, b.change.head_sha):
        reasons.append("Different base or head revision.")
    if effective_policy_hash(a.policy) != effective_policy_hash(b.policy):
        reasons.append("Different effective policy content.")
    if not a.policy_source or not b.policy_source:
        reasons.append("Policy source is missing for at least one evaluation.")
    elif a.policy_source != b.policy_source:
        reasons.append("Different policy sources.")
    if left.verdict.ruleset_version != right.verdict.ruleset_version:
        reasons.append("Different rule versions.")
    if a.collected_at != b.collected_at:
        reasons.append("Different collection cutoffs; external source states may differ.")
    first = {result.case: result for result in left.verdict.results}
    second = {result.case: result for result in right.verdict.results}
    findings = []
    for case in sorted(first.keys() | second.keys()):
        old, new = first.get(case), second.get(case)
        findings.append(
            {
                "case": case,
                "change": (
                    _finding_change(old, new)
                    if same_target and left.verdict.ruleset_version == right.verdict.ruleset_version
                    else "not_comparable"
                ),
                "before": old.model_dump(mode="json") if old else None,
                "after": new.model_dump(mode="json") if new else None,
            }
        )

    def side(case: Case, evaluation_id: str) -> dict[str, Any]:
        bundle = case.bundle
        return {
            "evaluation_id": evaluation_id,
            "bundle_hash": case.summary.bundle_hash,
            "repo": bundle.change.repo,
            "pr_number": bundle.change.pr_number,
            "base_sha": bundle.change.base_sha,
            "head_sha": bundle.change.head_sha,
            "cutoff": bundle.collected_at.isoformat(),
            "verdict": case.verdict.value,
            "policy_source": bundle.policy_source,
            "policy_hash": effective_policy_hash(bundle.policy),
            "ruleset_version": case.verdict.ruleset_version,
            "graph": {"entities": len(bundle.entities), "edges": len(bundle.edges)},
            "semantic": {"criteria": len(bundle.criteria), "candidates": len(bundle.candidates)},
        }

    return {
        "before": side(left, left_id),
        "after": side(right, right_id),
        "directly_comparable": not reasons,
        "limitations": reasons,
        "findings": findings,
        "graph_note": (
            "Legacy graph counts are shown; missing edge provenance prevents "
            "a reliable graph difference."
        ),
        "semantic_note": (
            "Legacy rankings lack gate and corpus provenance; "
            "semantic decisions cannot be compared."
        ),
    }
