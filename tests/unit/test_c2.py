"""Tests for C2 (PR modifies its own governance)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codeatlas.evaluate.rules.c2_pr_modifies_governance import C2PrModifiesGovernance
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    ChangeRef,
    EvidenceBundle,
    GovernanceChange,
    GovernanceSnapshot,
    IdentityMap,
    Policy,
    SemanticPolicy,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _policy(c2_severity: str | None = None) -> Policy:
    from codeatlas.schema import CasePolicy
    cases = {}
    if c2_severity is not None:
        cases["C2"] = CasePolicy(severity=c2_severity)  # type: ignore[arg-type]
    return Policy(
        version=1,
        workitem_key_pattern=r"[A-Z]+-\d+",
        requirement_fields=["summary", "description", "acceptance_criteria"],
        governance_paths=["CODEOWNERS", ".codeatlas/**", ".github/workflows/**"],
        approval=ApprovalPolicy(status="Approved", approver_account_ids=["acct-1"]),
        priority_authority=AuthorityPolicy(account_ids=["acct-1"]),
        cases=cases,
        semantic=SemanticPolicy(top_k=5),
    )


def _empty_governance() -> GovernanceSnapshot:
    return GovernanceSnapshot(
        evidence_id="governance:base",
        source="analysis",
        source_ref="base@base",
        retrieved_at=NOW,
        extractor_version="governance@0.1.0",
        base_sha="base",
        codeowners_path="CODEOWNERS",
        owner_rules=[],
        adrs=[],
        policy_blob_sha=None,
    )


def _bundle(policy: Policy, changes: list[GovernanceChange]) -> EvidenceBundle:
    return EvidenceBundle(
        schema_version="0",
        change=ChangeRef(repo="x/y", pr_number=1, base_sha="base", head_sha="head",
                        merge_base_sha="base"),
        codeatlas_version="0.1.0",
        collected_at=NOW,
        run_id="run-1",
        extractor_versions={},
        model_ids=[],
        model_file_hashes={},
        policy=policy,
        identity_map=IdentityMap(people=[]),
        work_items=[], change_events=[], versions=[], lifecycle=[],
        commits=[], reviews=[], people=[],
        governance=_empty_governance(),
        governance_changes=changes,
        entities=[], changed=[], edges=[],
        criteria=[], candidates=[],
        collection_notes=[],
    )


def _change(path: str) -> GovernanceChange:
    return GovernanceChange(
        evidence_id=f"governance_change:{path}",
        source="analysis", source_ref=f"diff:{path}",
        retrieved_at=NOW, extractor_version="governance@0.1.0",
        path=path, change_type="modified",
    )


def test_satisfied_when_no_governance_touched() -> None:
    rule = C2PrModifiesGovernance()
    bundle = _bundle(_policy(), [])
    result = rule.check(bundle, [], bundle.policy)
    assert result.case == "C2"
    assert result.outcome == "SATISFIED"
    assert result.severity == "none"


def test_violated_when_codeowners_edited() -> None:
    rule = C2PrModifiesGovernance()
    bundle = _bundle(_policy(), [_change("CODEOWNERS")])
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert result.severity == "review"
    assert "CODEOWNERS" in result.message
    assert result.evidence_ids == ["governance_change:CODEOWNERS"]


def test_violated_lists_up_to_five_paths_then_more_count() -> None:
    rule = C2PrModifiesGovernance()
    changes = [_change(f".codeatlas/{i}.yaml") for i in range(7)]
    bundle = _bundle(_policy(), changes)
    result = rule.check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert "+2 more" in result.message


def test_severity_overridden_by_policy() -> None:
    rule = C2PrModifiesGovernance()
    bundle = _bundle(_policy(c2_severity="block"), [_change("CODEOWNERS")])
    result = rule.check(bundle, [], bundle.policy)
    assert result.severity == "block"


def test_satisfied_result_has_none_severity_and_no_evidence_ids() -> None:
    rule = C2PrModifiesGovernance()
    bundle = _bundle(_policy(), [])
    result = rule.check(bundle, [], bundle.policy)
    assert result.severity == "none"
    assert result.evidence_ids == []
