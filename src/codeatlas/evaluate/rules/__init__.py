"""Rule registry (spec §6 `evaluate.rules`). Phase 1 rules: C1, C3, C6, C8 and the C7 stub."""

from collections.abc import Iterable

from codeatlas.evaluate.rules.base import Rule
from codeatlas.evaluate.rules.c1_changed_after_impl import ChangedAfterImplementation
from codeatlas.evaluate.rules.c3_not_approved import NotApproved
from codeatlas.evaluate.rules.c6_superseded_version import SupersededVersion
from codeatlas.evaluate.rules.c7_test_evidence import TestEvidenceStub
from codeatlas.evaluate.rules.c8_priority_authority import PriorityWithoutAuthority


def default_rules() -> tuple[Rule, ...]:
    """The rules every evaluation runs, in no particular order (order never matters)."""
    return (
        ChangedAfterImplementation(),
        NotApproved(),
        SupersededVersion(),
        TestEvidenceStub(),
        PriorityWithoutAuthority(),
    )


def ruleset_version(rules: Iterable[Rule]) -> str:
    """`"C1@1,C3@2"`: the cases and versions that produced a verdict; `"none"` if empty."""
    parts = sorted(f"{rule.case}@{rule.version}" for rule in rules)
    return ",".join(parts) or "none"
