"""C3: requirement not approved, or the approval is stale (FR-6.3; spec AC08; step-7 spec)."""

from codeatlas.evaluate.rules.base import (
    NOTE_APPROVERS_UNKNOWN,
    KeyResult,
    combine,
    facts_for,
    history_broken,
    no_work_item,
    requirement_edits,
    severity,
    work_item_keys,
)
from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink


class NotApproved:
    case: CaseId = "C3"
    version: str = "1"
    required_evidence: tuple[str, ...] = (
        "work_items",
        "lifecycle",
        "change_events",
        "versions",
        "collection_notes",
    )

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        rule_severity = severity(policy, self.case, "block")
        keys = work_item_keys(bundle)
        if not keys:
            return no_work_item(self.case, rule_severity)
        return combine(
            self.case, {key: self._judge(bundle, key, policy) for key in keys}, rule_severity
        )

    def _judge(self, bundle: EvidenceBundle, key: str, policy: Policy) -> KeyResult:
        if f"{NOTE_APPROVERS_UNKNOWN}{key}" in bundle.collection_notes:
            return KeyResult("INSUFFICIENT_EVIDENCE", "approvers could not be resolved", [])
        facts = facts_for(bundle, key)
        if facts is None:
            return KeyResult("INSUFFICIENT_EVIDENCE", "issue not found", [])
        approved_at = facts.latest_valid_approval_at
        if approved_at is None:
            attempts = [a.evidence_id for a in facts.approvals]
            return KeyResult("VIOLATED", "not approved by an approver", attempts)
        approval = next(
            a
            for a in facts.approvals
            if a.at == approved_at and a.by_approver_group and a.revoked_at is None
        )
        edited_at = facts.last_requirement_edit_at
        if edited_at is not None and edited_at > approved_at:
            later = [e for e in requirement_edits(bundle, key, policy) if e.at > approved_at]
            return KeyResult(
                "VIOLATED",
                "stale approval: the requirement changed after it was approved",
                [approval.evidence_id, *(e.evidence_id for e in later)],
            )
        if history_broken(bundle, key):
            return KeyResult("INSUFFICIENT_EVIDENCE", "history incomplete: an unlogged change", [])
        return KeyResult("SATISFIED", "approved, and unchanged since", [approval.evidence_id])
