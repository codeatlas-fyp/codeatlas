"""The rule interface (spec §7.2, §15; step-4 spec "Rule contract")."""

from typing import Protocol

from codeatlas.schema import CaseId, CheckResult, EvidenceBundle, Policy, TraceLink


class Rule(Protocol):
    """One contradiction case as a pure function over a frozen bundle.

    No I/O, no clock, no randomness: time comes from the bundle (`collected_at`, event times).
    """

    case: CaseId
    version: str
    required_evidence: tuple[str, ...]  # EvidenceBundle fields the rule reads

    def check(
        self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy
    ) -> CheckResult: ...
