"""Rule registry (spec §6 `evaluate.rules`): C1-C9, with C7 still a stub."""

from collections.abc import Iterable

from codeatlas.evaluate.rules.base import Rule
from codeatlas.evaluate.rules.c1_changed_after_impl import ChangedAfterImplementation
from codeatlas.evaluate.rules.c2_pr_modifies_governance import C2PrModifiesGovernance
from codeatlas.evaluate.rules.c3_not_approved import NotApproved
from codeatlas.evaluate.rules.c4_assignee_not_implementer import AssigneeNotImplementer
from codeatlas.evaluate.rules.c5_legacy import C5Legacy
from codeatlas.evaluate.rules.c5_owner_did_not_approve import (
    C5CompleteFileInventory,
    C5OwnerDidNotApprove,
)
from codeatlas.evaluate.rules.c6_superseded_version import SupersededVersion
from codeatlas.evaluate.rules.c7_test_evidence import FrozenTestEvidenceStub, TestEvidenceStub
from codeatlas.evaluate.rules.c8_priority_authority import PriorityWithoutAuthority
from codeatlas.evaluate.rules.c9_unexplained_change import (
    C9Advisory,
    C9SemanticRun,
    C9UnexplainedChange,
)
from codeatlas.evaluate.rules.frozen_cutoff import FrozenCutoffRule


def default_rules(
    *, legacy_c5: bool = False, complete_file_inventory: bool = False, semantic_run: bool = False,
    advisory_c9: bool = False,
    frozen_cutoff: bool = False,
) -> tuple[Rule, ...]:
    """The rules every evaluation runs, in no particular order (order never matters)."""
    rules: tuple[Rule, ...] = (
        ChangedAfterImplementation(),
        C2PrModifiesGovernance(),
        NotApproved(),
        AssigneeNotImplementer(),
        SupersededVersion(),
        FrozenTestEvidenceStub() if frozen_cutoff else TestEvidenceStub(),
        PriorityWithoutAuthority(),
        C5Legacy()
        if legacy_c5
        else (C5CompleteFileInventory() if complete_file_inventory else C5OwnerDidNotApprove()),
        C9Advisory()
        if advisory_c9
        else (C9SemanticRun() if semantic_run else C9UnexplainedChange()),
    )
    if frozen_cutoff:
        return tuple(
            FrozenCutoffRule(rule) if rule.case in {"C1", "C3", "C4", "C6", "C8"} else rule
            for rule in rules
        )
    return rules


def ruleset_version(rules: Iterable[Rule]) -> str:
    """`"C1@1,C3@2"`: the cases and versions that produced a verdict; `"none"` if empty."""
    parts = sorted(f"{rule.case}@{rule.version}" for rule in rules)
    return ",".join(parts) or "none"
