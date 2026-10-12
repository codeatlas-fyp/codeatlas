"""C1: requirement changed after implementation began (FR-6.1; step-7 spec)."""

from codeatlas.evaluate.rules.base import (
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


class ChangedAfterImplementation:
    case: CaseId = "C1"
    version: str = "1"
    required_evidence: tuple[str, ...] = (
        "work_items",
        "change_events",
        "commits",
        "versions",
        "lifecycle",
    )

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        rule_severity = severity(policy, self.case, "review")
        keys = work_item_keys(bundle)
        if not keys:
            return no_work_item(self.case, rule_severity)
        if not bundle.commits:
            missing = KeyResult(
                "INSUFFICIENT_EVIDENCE", "no commits, so no implementation start", []
            )
            return combine(self.case, dict.fromkeys(keys, missing), rule_severity)
        first = min(bundle.commits, key=lambda c: (c.committed_at, c.sha))
        results = {}
        for key in keys:
            later = [e for e in requirement_edits(bundle, key, policy) if e.at > first.committed_at]
            if facts_for(bundle, key) is None:
                results[key] = KeyResult("INSUFFICIENT_EVIDENCE", "issue not found", [])
            elif later:
                fields = ", ".join(sorted({e.field for e in later}))
                results[key] = KeyResult(
                    "VIOLATED",
                    f"{fields} changed {len(later)} time(s) after the first commit",
                    [first.evidence_id, *(e.evidence_id for e in later)],
                )
            elif history_broken(bundle, key):
                results[key] = KeyResult(
                    "INSUFFICIENT_EVIDENCE", "history incomplete: an unlogged change", []
                )
            else:
                results[key] = KeyResult("SATISFIED", "no change after the first commit", [])
        return combine(self.case, results, rule_severity)
