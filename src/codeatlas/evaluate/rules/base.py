"""The rule interface and the judgement helpers shared by rules (spec §7.2, §15; step-7 spec).

Rules are pure: no I/O, no clock, no randomness. Time comes from the bundle.
"""

from typing import Literal, NamedTuple, Protocol

from codeatlas.schema import (
    CaseId,
    ChangeEvent,
    CheckResult,
    EvidenceBundle,
    LifecycleFacts,
    Policy,
    TraceLink,
)

Severity = Literal["block", "review", "none"]
KeyOutcome = Literal["SATISFIED", "VIOLATED", "INSUFFICIENT_EVIDENCE"]

# Collection note the pipeline adds when it cannot tell who the approvers are (step-7 spec rule 7).
NOTE_APPROVERS_UNKNOWN = "approvers unknown: "


class Rule(Protocol):
    """One contradiction case as a pure function over a frozen bundle."""

    case: CaseId
    version: str
    required_evidence: tuple[str, ...]  # EvidenceBundle fields the rule reads

    def check(
        self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy
    ) -> CheckResult: ...


class KeyResult(NamedTuple):
    """The judgement for one requirement key."""

    outcome: KeyOutcome
    reason: str
    evidence_ids: list[str]


def work_item_keys(bundle: EvidenceBundle) -> list[str]:
    return sorted({item.key for item in bundle.work_items})


def severity(policy: Policy, case: CaseId, default: Severity) -> Severity:
    configured = policy.cases.get(case)
    return configured.severity if configured and configured.severity else default


def facts_for(bundle: EvidenceBundle, key: str) -> LifecycleFacts | None:
    return next((facts for facts in bundle.lifecycle if facts.key == key), None)


def history_broken(bundle: EvidenceBundle, key: str) -> bool:
    """A version with unresolved content means an unlogged change at an unknown time."""
    return any(not v.content_resolved for v in bundle.versions if v.key == key)


def requirement_edits(bundle: EvidenceBundle, key: str, policy: Policy) -> list[ChangeEvent]:
    fields = set(policy.requirement_fields)
    edits = [e for e in bundle.change_events if e.key == key and e.field in fields]
    return sorted(edits, key=lambda e: (e.at, e.evidence_id))


def no_work_item(case: CaseId, rule_severity: Severity) -> CheckResult:
    return CheckResult(
        case=case,
        outcome="INSUFFICIENT_EVIDENCE",
        severity=rule_severity,
        required=True,
        message="no work item: no Jira key in the branch, PR or commits",
        evidence_ids=[],
    )


def combine(case: CaseId, results: dict[str, KeyResult], rule_severity: Severity) -> CheckResult:
    """VIOLATED if any key is, else INSUFFICIENT_EVIDENCE if any key is, else SATISFIED."""
    outcomes = {r.outcome for r in results.values()}
    outcome: KeyOutcome = (
        "VIOLATED"
        if "VIOLATED" in outcomes
        else "INSUFFICIENT_EVIDENCE"
        if "INSUFFICIENT_EVIDENCE" in outcomes
        else "SATISFIED"
    )
    message = "; ".join(f"{key}: {results[key].reason}" for key in sorted(results))
    evidence = sorted({i for r in results.values() for i in r.evidence_ids})
    return CheckResult(
        case=case,
        outcome=outcome,
        severity=rule_severity,
        required=True,
        message=message,
        evidence_ids=evidence,
    )
