"""Rule registry (spec §6 `evaluate.rules`): C1-C9, with C7 still a stub."""

from collections.abc import Iterable

from codeatlas.evaluate.rules.base import Rule
from codeatlas.evaluate.rules.c1_changed_after_impl import ChangedAfterImplementation
from codeatlas.evaluate.rules.c2_pr_modifies_governance import C2PrModifiesGovernance
from codeatlas.evaluate.rules.c3_not_approved import NotApproved
from codeatlas.evaluate.rules.c4_assignee_not_implementer import AssigneeNotImplementer
from codeatlas.evaluate.rules.c5_owner_did_not_approve import C5OwnerDidNotApprove
from codeatlas.evaluate.rules.c6_superseded_version import SupersededVersion
from codeatlas.evaluate.rules.c7_test_evidence import TestEvidenceStub
from codeatlas.evaluate.rules.c8_priority_authority import PriorityWithoutAuthority
from codeatlas.evaluate.rules.c9_unexplained_change import C9UnexplainedChange


def default_rules() -> tuple[Rule, ...]:
    """The rules every evaluation runs, in no particular order (order never matters)."""
    return (
        ChangedAfterImplementation(),
        C2PrModifiesGovernance(),
        NotApproved(),
        AssigneeNotImplementer(),
        SupersededVersion(),
        TestEvidenceStub(),
        PriorityWithoutAuthority(),
        C5OwnerDidNotApprove(),
        C9UnexplainedChange(),
    )


def ruleset_version(rules: Iterable[Rule]) -> str:
    """`"C1@1,C3@2"`: the cases and versions that produced a verdict; `"none"` if empty."""
    parts = sorted(f"{rule.case}@{rule.version}" for rule in rules)
    return ",".join(parts) or "none"
