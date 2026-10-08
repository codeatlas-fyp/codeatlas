"""C2 — PR modifies its own governance (spec §3 case C2, step-10).

Trigger: `bundle.governance_changes` is non-empty — i.e. the PR touched at
least one file whose path matched a `policy.governance_paths` glob at the
base commit.

Severity: review (configurable via policy.cases["C2"].severity).

Outcome:
    - VIOLATED  if any governance change was collected.
    - SATISFIED if the diff touched no governance paths.
    - never NOT_APPLICABLE: the check is always meaningful.

Pure function. No I/O, no clock. Time comes from the evidence itself.
"""

from __future__ import annotations

from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink

CASE: CaseId = "C2"
VERSION = "1"
REQUIRED_EVIDENCE = ("governance_changes",)


class C2PrModifiesGovernance:
    """Standalone class so the rule registry can introspect `case` and `version`."""

    case: CaseId = CASE
    version: str = VERSION
    required_evidence: tuple[str, ...] = REQUIRED_EVIDENCE

    def check(
        self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy
    ) -> CheckResult:
        severity = _severity_for(policy)
        changes = list(bundle.governance_changes)
        if not changes:
            return CheckResult(
                case=CASE,
                outcome="SATISFIED",
                severity="none",
                required=False,
                message="No governance files were modified by this PR.",
                evidence_ids=[],
            )
        paths = sorted({change.path for change in changes})
        return CheckResult(
            case=CASE,
            outcome="VIOLATED",
            severity=severity,
            required=False,
            message=(
                f"PR modifies {len(paths)} governance file(s): "
                + ", ".join(paths[:5])
                + (f" (+{len(paths) - 5} more)" if len(paths) > 5 else "")
                + ". All other rules still use the base-branch versions."
            ),
            evidence_ids=sorted({change.evidence_id for change in changes}),
        )


def _severity_for(policy: Policy) -> str:
    case_policy = policy.cases.get("C2")
    if case_policy and case_policy.severity:
        return case_policy.severity
    return "review"
