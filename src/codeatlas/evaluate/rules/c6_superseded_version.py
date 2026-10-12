"""C6: code linked to a superseded requirement version (FR-6.6; step-7 spec).

The requirement changed after the PR's last commit, so the code implements an older version.
"""

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


class SupersededVersion:
    case: CaseId = "C6"
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
            missing = KeyResult("INSUFFICIENT_EVIDENCE", "no commits, so no last commit", [])
            return combine(self.case, dict.fromkeys(keys, missing), rule_severity)
        last = max(bundle.commits, key=lambda c: (c.committed_at, c.sha))
        results = {}
        for key in keys:
            later = [e for e in requirement_edits(bundle, key, policy) if e.at > last.committed_at]
            if facts_for(bundle, key) is None:
                results[key] = KeyResult("INSUFFICIENT_EVIDENCE", "issue not found", [])
            elif later:
                results[key] = KeyResult(
                    "VIOLATED",
                    "the requirement changed after the last commit: the code implements a "
                    "superseded version",
                    [last.evidence_id, *(e.evidence_id for e in later)],
                )
            elif history_broken(bundle, key):
                results[key] = KeyResult(
                    "INSUFFICIENT_EVIDENCE", "history incomplete: an unlogged change", []
                )
            else:
                results[key] = KeyResult("SATISFIED", "no change after the last commit", [])
        return combine(self.case, results, rule_severity)
