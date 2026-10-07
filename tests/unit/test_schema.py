"""Tests for docs/specs/step-2-schema.md (AC1-AC8). Samples are schema objects built in code."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import codeatlas.schema as schema
from codeatlas.schema import (
    Adr,
    Approval,
    ApprovalPolicy,
    AuthorityPolicy,
    CasePolicy,
    ChangedEntity,
    ChangeEvent,
    ChangeRef,
    CheckResult,
    CodeEdge,
    CodeEntity,
    Commit,
    Criterion,
    Evidence,
    EvidenceBundle,
    GovernanceChange,
    GovernanceSnapshot,
    IdentityEntry,
    IdentityMap,
    LifecycleFacts,
    OwnerRule,
    Policy,
    PriorityChange,
    RequirementVersion,
    ResolvedPerson,
    Review,
    SemanticCandidate,
    SemanticPolicy,
    TraceLink,
    Verdict,
    WorkItemRef,
)

T0 = datetime(2026, 10, 3, 4, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def evidence(evidence_id: str, source: str = "jira") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source": source,
        "source_ref": "https://example.atlassian.net/browse/SBX-2",
        "retrieved_at": T1,
        "extractor_version": "0.1.0",
    }


EVIDENCE = Evidence(**evidence("jira:SBX-2:issue"))
CHANGE_REF = ChangeRef(
    repo="codeatlas-fyp/codeatlas-sandbox",
    pr_number=2,
    base_sha="a" * 40,
    head_sha="b" * 40,
    merge_base_sha="c" * 40,
)
WORK_ITEM = WorkItemRef(
    **evidence("github:pr:2:title:SBX-2", "github"),
    key="SBX-2",
    found_in="pr_title",
    commit_sha=None,
)
CHANGE_EVENT = ChangeEvent(
    **evidence("jira:SBX-2:history:10045:description"),
    key="SBX-2",
    history_id="10045",
    field="description",
    from_value="A member returns a book.",
    to_value="A member returns a book and the loan is closed.",
    actor_account_id="user-01",
    at=T1,
)
VERSION = RequirementVersion(
    key="SBX-2",
    version_no=2,
    valid_from=T1,
    valid_to=None,
    summary="Return a book",
    description=None,
    acceptance_criteria="1. The loan is closed.",
    status="Approved",
    priority="Medium",
    assignee_account_id="user-02",
    content_resolved=False,
    unresolved_reasons=["description: 'to' text of history 10044 does not match"],
    unresolved_fields=["description"],
)
APPROVAL = Approval(
    key="SBX-2",
    actor_account_id="user-01",
    at=T0,
    history_id="10040",
    by_approver_group=True,
    revoked_at=None,
    evidence_id="jira:SBX-2:history:10040:status",
)
PRIORITY_CHANGE = PriorityChange(
    key="SBX-4",
    actor_account_id="user-03",
    at=T1,
    from_priority="Medium",
    to_priority="Highest",
    after_first_approval=True,
    actor_has_authority=False,
    evidence_id="jira:SBX-4:history:10046:priority",
)
LIFECYCLE = LifecycleFacts(
    key="SBX-2",
    requester_account_id="user-01",
    approvals=[APPROVAL],
    latest_valid_approval_at=T0,
    last_requirement_edit_at=T1,
    priority_changes=[PRIORITY_CHANGE],
    assignee_history=[(T0, None), (T1, "user-02")],
    labels=["requires-test-evidence"],
)
COMMIT = Commit(
    **evidence("git:commit:" + "d" * 40, "git"),
    sha="d" * 40,
    author_email="user-02@example.test",
    author_login="user-02",
    committed_at=T1,
    message="SBX-2: close the loan on return",
)
REVIEW = Review(
    **evidence("github:pr:2:review:1", "github"),
    reviewer_login="user-01",
    state="APPROVED",
    commit_id="b" * 40,
    submitted_at=T1,
)
PERSON = ResolvedPerson(
    git_email="user-02@example.test", github_login="user-02", jira_account_id="user-02"
)
OWNER_RULE = OwnerRule(pattern="/library/loans/", owners=["@user-01"], line=4)
ADR = Adr(
    adr_id="ADR-0001",
    path="docs/adr/0001-loans.md",
    status="accepted",
    scope=["library/loans/**"],
    requirement_keys=["SBX-2"],
)
GOVERNANCE = GovernanceSnapshot(
    **evidence("git:governance:" + "a" * 40, "git"),
    base_sha="a" * 40,
    codeowners_path="CODEOWNERS",
    owner_rules=[OWNER_RULE],
    adrs=[ADR],
    policy_blob_sha="e" * 40,
)
GOVERNANCE_CHANGE = GovernanceChange(
    **evidence("git:change:CODEOWNERS", "git"), path="CODEOWNERS", change_type="modified"
)
ENTITY = CodeEntity(
    entity_id="library.loans.return_book",
    kind="function",
    file="library/loans.py",
    start_line=10,
    end_line=30,
    signature="def return_book(loan_id: str) -> None",
    docstring=None,
)
CHANGED = ChangedEntity(
    **evidence("analysis:changed:library/loans.py:12", "analysis"),
    entity_id="library.loans.return_book",
    change="modified",
    file="library/loans.py",
    hunk_lines=(12, 18),
    commit_shas=["d" * 40],
)
EDGE = CodeEdge(
    src="library.loans.return_book", dst="library.fees.charge", kind="CALLS", confident=True
)
CRITERION = Criterion(
    criterion_id="SBX-2:v2:ac1",
    key="SBX-2",
    version_no=2,
    text="The loan is closed.",
    derived_from_description=False,
)
CANDIDATE = SemanticCandidate(
    criterion_id="SBX-2:v2:ac1",
    entity_id="library.loans.return_book",
    bm25_rank=1,
    dense_rank=None,
    fused_rank=1,
    fused_score=0.0328,
    model_id="all-MiniLM-L6-v2@rev1",
)
TRACE_LINK = TraceLink(
    requirement_key="SBX-2",
    criterion_id=None,
    entity_id="library.loans.return_book",
    state="OBSERVED",
    evidence_ids=["git:commit:" + "d" * 40],
    reasons=["commit carrying SBX-2 modified the entity"],
)
CHECK = CheckResult(
    case="C3",
    outcome="VIOLATED",
    severity="block",
    required=True,
    message="stale approval",
    evidence_ids=[APPROVAL.evidence_id, CHANGE_EVENT.evidence_id],
)
VERDICT = Verdict(
    value="FAIL",
    results=[CHECK],
    ruleset_version="0.1.0",
    bundle_hash="f" * 64,
    verdict_hash="0" * 64,
)
APPROVAL_POLICY = ApprovalPolicy(status="Approved", approver_account_ids=["user-01"])
AUTHORITY_POLICY = AuthorityPolicy(group="sbx-priority-authority")
CASE_POLICY = CasePolicy(severity="block")
SEMANTIC_POLICY = SemanticPolicy(top_k=5)
POLICY = Policy(
    version=1,
    workitem_key_pattern="[A-Z]+-\d+",
    requirement_fields=["summary", "description"],
    governance_paths=["CODEOWNERS", ".codeatlas/**"],
    approval=APPROVAL_POLICY,
    priority_authority=AUTHORITY_POLICY,
    cases={"C3": CASE_POLICY, "C7": CasePolicy(required_when_label="requires-test-evidence")},
    semantic=SEMANTIC_POLICY,
)
IDENTITY_ENTRY = IdentityEntry(
    jira_account_id="user-02", github_login="user-02", git_emails=["user-02@example.test"]
)
IDENTITY_MAP = IdentityMap(people=[IDENTITY_ENTRY])
BUNDLE = EvidenceBundle(
    schema_version="0",
    change=CHANGE_REF,
    codeatlas_version="0.1.0",
    collected_at=T1,
    run_id="run-0001",
    extractor_versions={"jira": "0.1.0"},
    model_ids=[CANDIDATE.model_id],
    model_file_hashes={CANDIDATE.model_id: "9" * 64},
    policy=POLICY,
    identity_map=IDENTITY_MAP,
    work_items=[WORK_ITEM],
    change_events=[CHANGE_EVENT],
    versions=[VERSION],
    lifecycle=[LIFECYCLE],
    commits=[COMMIT],
    reviews=[REVIEW],
    people=[PERSON],
    governance=GOVERNANCE,
    governance_changes=[GOVERNANCE_CHANGE],
    entities=[ENTITY],
    changed=[CHANGED],
    edges=[EDGE],
    criteria=[CRITERION],
    candidates=[CANDIDATE],
    collection_notes=["work item SBX-99 not found"],
)

SAMPLES: list[BaseModel] = [
    EVIDENCE,
    CHANGE_REF,
    WORK_ITEM,
    CHANGE_EVENT,
    VERSION,
    APPROVAL,
    PRIORITY_CHANGE,
    LIFECYCLE,
    COMMIT,
    REVIEW,
    PERSON,
    OWNER_RULE,
    ADR,
    GOVERNANCE,
    GOVERNANCE_CHANGE,
    ENTITY,
    CHANGED,
    EDGE,
    CRITERION,
    CANDIDATE,
    TRACE_LINK,
    CHECK,
    VERDICT,
    APPROVAL_POLICY,
    AUTHORITY_POLICY,
    CASE_POLICY,
    SEMANTIC_POLICY,
    POLICY,
    IDENTITY_ENTRY,
    IDENTITY_MAP,
    BUNDLE,
]


def sample_id(sample: BaseModel) -> str:
    return type(sample).__name__


# --- AC1: round trip, and every exported model has a sample ------------------------------------


def test_ac1_every_exported_model_has_a_sample() -> None:
    exported = {
        name
        for name in schema.__all__
        if isinstance(getattr(schema, name), type) and issubclass(getattr(schema, name), BaseModel)
    }

    assert exported == {type(sample).__name__ for sample in SAMPLES}


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac1_json_round_trip(sample: BaseModel) -> None:
    first = sample.model_dump_json()

    loaded = type(sample).model_validate_json(first)

    assert loaded == sample
    assert loaded.model_dump_json() == first


# --- AC2-AC3: unknown fields rejected, frozen --------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac2_unknown_field_rejected(sample: BaseModel) -> None:
    data = {**sample.model_dump(), "unexpected": 1}

    with pytest.raises(ValidationError, match="unexpected"):
        type(sample).model_validate(data)


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac3_models_are_frozen(sample: BaseModel) -> None:
    field = next(iter(type(sample).model_fields))

    with pytest.raises(ValidationError, match="frozen"):
        setattr(sample, field, getattr(sample, field))


# --- AC4: datetimes are UTC-aware ---------------------------------------------------------------


def test_ac4_naive_datetime_rejected() -> None:
    naive = datetime(2026, 10, 3, 9, 0)  # noqa: DTZ001

    with pytest.raises(ValidationError, match="timezone"):
        Approval.model_validate({**APPROVAL.model_dump(), "at": naive})


def test_ac4_naive_datetime_rejected_inside_tuples() -> None:
    naive = [(datetime(2026, 10, 3, 9, 0), "user-02")]  # noqa: DTZ001

    with pytest.raises(ValidationError, match="timezone"):
        LifecycleFacts.model_validate({**LIFECYCLE.model_dump(), "assignee_history": naive})


def test_ac4_aware_datetime_converted_to_utc() -> None:
    plus_five = datetime(2026, 10, 3, 9, 0, tzinfo=timezone(timedelta(hours=5)))

    approval = Approval.model_validate({**APPROVAL.model_dump(), "at": plus_five})

    assert approval.at == T0
    assert approval.at.utcoffset() == timedelta(0)
    assert '"at":"2026-10-03T04:00:00Z"' in approval.model_dump_json()


# --- AC5: literal values are closed -------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "field", "value"),
    [
        (TRACE_LINK, "state", "LINKED"),
        (REVIEW, "state", "PENDING"),
        (WORK_ITEM, "found_in", "title"),
        (EVIDENCE, "source", "linear"),
        (VERSION, "unresolved_fields", ["status"]),
        (BUNDLE, "schema_version", "1"),
        (CHECK, "case", "C10"),
        (CHECK, "outcome", "satisfied"),
        (VERDICT, "value", "PASSED"),
    ],
    ids=[
        "link-state",
        "review-state",
        "found-in",
        "source",
        "requirement-field",
        "schema-version",
        "case-id",
        "outcome",
        "verdict",
    ],
)
def test_ac5_unknown_literal_values_rejected(sample: BaseModel, field: str, value: Any) -> None:
    with pytest.raises(ValidationError, match=field):
        type(sample).model_validate({**sample.model_dump(), field: value})


# --- AC6: the whole bundle --------------------------------------------------------------------


def test_ac6_bundle_dumps_identically_and_round_trips() -> None:
    first = BUNDLE.model_dump_json()

    assert BUNDLE.model_dump_json() == first
    assert EvidenceBundle.model_validate_json(first) == BUNDLE
    assert EvidenceBundle.model_validate_json(first).model_dump_json() == first


def test_ac6_bundle_has_no_bundle_hash_field() -> None:
    # S3: the bundle is content-addressed, so its hash cannot live inside it.
    assert "bundle_hash" not in EvidenceBundle.model_fields


# --- AC8: approvers and priority authority (S1) -------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        ApprovalPolicy(status="Approved", approver_group="sbx-requirement-approvers"),
        ApprovalPolicy(status="Approved", approver_account_ids=["user-01"]),
        ApprovalPolicy(status="Approved", approver_group="g", approver_account_ids=["user-01"]),
        AuthorityPolicy(group="sbx-priority-authority"),
        AuthorityPolicy(account_ids=["user-01", "user-02"]),
    ],
    ids=["approval-group", "approval-ids", "approval-both", "authority-group", "authority-ids"],
)
def test_ac8_group_or_account_ids_accepted(policy: BaseModel) -> None:
    assert type(policy).model_validate(policy.model_dump()) == policy


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (ApprovalPolicy, {"status": "Approved"}),
        (ApprovalPolicy, {"status": "Approved", "approver_account_ids": []}),
        (AuthorityPolicy, {}),
        (AuthorityPolicy, {"group": None, "account_ids": []}),
    ],
    ids=["approval-nothing", "approval-empty-ids", "authority-nothing", "authority-empty-ids"],
)
def test_ac8_neither_group_nor_account_ids_rejected(
    model: type[BaseModel], data: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError, match="group or at least one account id"):
        model.model_validate(data)


def test_ac8_policy_requires_priority_authority() -> None:
    data = POLICY.model_dump()
    del data["priority_authority"]

    with pytest.raises(ValidationError, match="priority_authority"):
        Policy.model_validate({**data, "priority_authority_group": "sbx-priority-authority"})


# --- AC7: version contents (S5) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_resolved", "unresolved_fields"),
    [(True, ["description"]), (False, [])],
)
def test_ac7_content_resolved_must_match_unresolved_fields(
    content_resolved: bool, unresolved_fields: list[str]
) -> None:
    data = {
        **VERSION.model_dump(),
        "content_resolved": content_resolved,
        "unresolved_fields": unresolved_fields,
    }

    with pytest.raises(ValidationError, match="content_resolved"):
        RequirementVersion.model_validate(data)


def test_ac7_unresolved_field_must_have_no_text() -> None:
    data = {**VERSION.model_dump(), "description": "guessed text"}

    with pytest.raises(ValidationError, match="description"):
        RequirementVersion.model_validate(data)


def test_ac7_fully_resolved_version_is_valid() -> None:
    data = {
        **VERSION.model_dump(),
        "description": "A member returns a book.",
        "content_resolved": True,
        "unresolved_reasons": [],
        "unresolved_fields": [],
    }

    assert RequirementVersion.model_validate(data).content_resolved
