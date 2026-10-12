"""C8: priority changed after approval by someone without authority (step-7 spec)."""

from codeatlas.evaluate.rules.base import (
    KeyResult,
    combine,
    facts_for,
    no_work_item,
    severity,
    work_item_keys,
)
from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink


class PriorityWithoutAuthority:
    case: CaseId = "C8"
    version: str = "1"
    required_evidence: tuple[str, ...] = ("work_items", "lifecycle")

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        rule_severity = severity(policy, self.case, "review")
        keys = work_item_keys(bundle)
        if not keys:
            return no_work_item(self.case, rule_severity)
        results = {}
        for key in keys:
            facts = facts_for(bundle, key)
            if facts is None:
                results[key] = KeyResult("INSUFFICIENT_EVIDENCE", "issue not found", [])
                continue
            # A change by Jira itself (no actor) is automation, not a person without authority.
            by_people = [
                p
                for p in facts.priority_changes
                if p.after_first_approval and p.actor_account_id is not None
            ]
            unauthorised = [p.evidence_id for p in by_people if p.actor_has_authority is False]
            unknown = [p.evidence_id for p in by_people if p.actor_has_authority is None]
            if unauthorised:
                results[key] = KeyResult(
                    "VIOLATED", "priority changed after approval without authority", unauthorised
                )
            elif unknown:
                results[key] = KeyResult(
                    "INSUFFICIENT_EVIDENCE", "authority of the person is unknown", unknown
                )
            else:
                results[key] = KeyResult(
                    "SATISFIED", "no unauthorised priority change after approval", []
                )
        return combine(self.case, results, rule_severity)
