"""Schema objects built in code, shared by tests and scripts/make_sample_bundle.py.

Fake identities only (`user-01`, `user-01@example.test`); SBX keys match the real sandbox.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

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
