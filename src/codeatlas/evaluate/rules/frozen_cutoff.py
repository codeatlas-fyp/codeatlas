"""Conservative rules for cutoff snapshots without complete lifecycle evidence."""

from codeatlas.evaluate.rules.base import Rule, Severity, severity
from codeatlas.schema import CheckResult, EvidenceBundle, Policy, TraceLink

_REASONS = {
    "C1": "the cutoff snapshot does not establish the requirement's complete edit timeline",
    "C3": "historical workflow categories and approval identity are unavailable",
    "C4": "historical assignee transitions do not establish stable Jira account identities",
    "C6": "the cutoff snapshot does not establish the requirement's complete edit timeline",
    "C8": "historical workflow categories and approval identity are unavailable",
}
_FIELDS = {"C3": "status", "C4": "assignee", "C8": "priority"}


class FrozenCutoffRule:
    """Do not infer a lifecycle verdict from a point-in-time snapshot."""

    def __init__(self, original: Rule):
        self.original = original
        self.case = original.case
        self.version = f"{original.version}-frozen4"
        self.required_evidence = original.required_evidence

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        if not bundle.frozen_jira_cutoff:
            return self.original.check(bundle, links, policy)
        reasons = []
        for key, snapshot in sorted(bundle.frozen_jira_cutoff.items()):
            reason = _REASONS[self.case]
            field = _FIELDS.get(self.case)
            if field:
                state = snapshot.get("state", {}).get("fields", {}).get(field, {})
                if state.get("reconstruction") != "reconstructed" and state.get("note"):
                    reason += f"; {field}: {state['note']}"
            reasons.append(f"{key}: {reason}")
        default: Severity = "block" if self.case == "C3" else "review"
        return CheckResult(
            case=self.case,
            outcome="INSUFFICIENT_EVIDENCE",
            severity=severity(policy, self.case, default),
            required=True,
            message="; ".join(reasons),
            evidence_ids=[],
        )
