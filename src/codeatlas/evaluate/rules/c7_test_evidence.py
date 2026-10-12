"""C7 stub: changed behaviour has no test evidence (FR-6.7; step-7 spec).

The full rule needs test and coverage evidence (Yusra, step 13). Until then the stub is honest:
when the policy label asks for test evidence, the result is INSUFFICIENT_EVIDENCE (so UNKNOWN);
otherwise the case does not apply.
"""

from codeatlas.evaluate.rules.base import severity
from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink


class TestEvidenceStub:
    __test__ = False  # not a pytest test class, despite the name

    case: CaseId = "C7"
    version: str = "0-stub"
    required_evidence: tuple[str, ...] = ("lifecycle",)

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        case_policy = policy.cases.get(self.case)
        label = case_policy.required_when_label if case_policy else None
        labelled = sorted(f.key for f in bundle.lifecycle if label and label in f.labels)
        if labelled:
            return CheckResult(
                case=self.case,
                outcome="INSUFFICIENT_EVIDENCE",
                severity=severity(policy, self.case, "review"),
                required=True,
                message=f"{', '.join(labelled)} require test evidence, which is not collected yet",
                evidence_ids=[],
            )
        return CheckResult(
            case=self.case,
            outcome="NOT_APPLICABLE",
            severity=severity(policy, self.case, "review"),
            required=False,
            message="no requirement asks for test evidence",
            evidence_ids=[],
        )
