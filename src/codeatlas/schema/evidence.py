"""Evidence models collected for one pull request (spec §7.1).

Additions approved in docs/specs/step-2-schema.md: `Adr` (S2), `LifecycleFacts.labels` (S4),
`RequirementVersion.unresolved_fields` (S5), `Approval.evidence_id` and
`PriorityChange.evidence_id` (S6).
"""

from typing import Literal, Self

from pydantic import model_validator

from codeatlas.schema.types import LinkState, Model, RequirementField, UtcDatetime


class Evidence(Model):
    """Base for every fact in a bundle."""

    evidence_id: str  # e.g. "jira:SBX-3:history:10045:acceptance_criteria"
    source: Literal["jira", "github", "git", "analysis"]
    source_ref: str  # URL, or "path@sha:line"
    retrieved_at: UtcDatetime
    extractor_version: str


class ChangeRef(Model):
    repo: str  # "owner/name"
    pr_number: int
    base_sha: str
    head_sha: str
    merge_base_sha: str


class WorkItemRef(Evidence):
    key: str
    found_in: Literal["branch", "pr_title", "pr_body", "commit"]
    commit_sha: str | None


class ChangeEvent(Evidence):
    """One changed field in one Jira history entry."""

    key: str
    history_id: str
    field: str
    from_value: str | None
    to_value: str | None
    actor_account_id: str | None  # None = Jira automation
    at: UtcDatetime


class RequirementVersion(Model):
    key: str
    version_no: int
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None
    summary: str | None
    description: str | None
    acceptance_criteria: str | None
    status: str
    priority: str | None
    assignee_account_id: str | None
    content_resolved: bool  # False if the chain check failed for any field
    unresolved_reasons: list[str]
    unresolved_fields: list[RequirementField]  # their text is None, never guessed

    @model_validator(mode="after")
    def _resolution_is_consistent(self) -> Self:
        if self.content_resolved == bool(self.unresolved_fields):
            raise ValueError("content_resolved must equal 'no unresolved_fields'")
        for name in self.unresolved_fields:
            if getattr(self, name) is not None:
                raise ValueError(f"{name} is unresolved, so its text must be None")
        return self


class Approval(Model):
    key: str
    actor_account_id: str | None
    at: UtcDatetime
    history_id: str
    by_approver_group: bool
    revoked_at: UtcDatetime | None  # meaning decided in step 6 (lifecycle)
    evidence_id: str  # the status-change ChangeEvent


class PriorityChange(Model):
    key: str
    actor_account_id: str | None
    at: UtcDatetime
    from_priority: str | None
    to_priority: str | None
    after_first_approval: bool
    actor_has_authority: bool | None  # None = system actor
    evidence_id: str  # the priority ChangeEvent


class LifecycleFacts(Model):
    key: str
    requester_account_id: str | None  # Jira reporter; to verify on the first recording
    approvals: list[Approval]
    latest_valid_approval_at: UtcDatetime | None
    last_requirement_edit_at: UtcDatetime | None
    priority_changes: list[PriorityChange]
    assignee_history: list[tuple[UtcDatetime, str | None]]
    labels: list[str]  # issue labels at collection time (C7)


class Commit(Evidence):
    sha: str
    author_email: str
    author_login: str | None
    committed_at: UtcDatetime
    message: str


class Review(Evidence):
    reviewer_login: str
    state: Literal["APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"]
    commit_id: str
    submitted_at: UtcDatetime


class ResolvedPerson(Model):
    git_email: str | None
    github_login: str | None
    jira_account_id: str | None  # None = UNRESOLVED


class OwnerRule(Model):
    pattern: str
    owners: list[str]
    line: int


class Adr(Model):
    adr_id: str
    path: str
    status: str | None
    scope: list[str]  # path globs the decision covers
    requirement_keys: list[str]


class GovernanceSnapshot(Evidence):
    base_sha: str
    codeowners_path: str | None
    owner_rules: list[OwnerRule]
    adrs: list[Adr]
    policy_blob_sha: str | None


class GovernanceChange(Evidence):
    path: str
    change_type: Literal["added", "modified", "deleted", "renamed"]


class CodeEntity(Model):
    entity_id: str  # "pkg.module.Class.method"
    kind: Literal["module", "class", "function", "method"]
    file: str
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None


class ChangedEntity(Evidence):
    entity_id: str | None  # None when a hunk could not be mapped
    change: Literal["added", "modified", "deleted", "unresolved"]
    file: str
    hunk_lines: tuple[int, int]
    commit_shas: list[str]  # commits that touched it


class CodeEdge(Model):
    src: str
    dst: str
    kind: Literal["IMPORTS", "CALLS"]
    confident: bool


class Criterion(Model):
    criterion_id: str  # "SBX-3:v2:ac1"
    key: str
    version_no: int
    text: str
    derived_from_description: bool


class SemanticCandidate(Model):
    criterion_id: str
    entity_id: str
    bm25_rank: int | None
    dense_rank: int | None
    fused_rank: int
    fused_score: float  # rounded to 4 decimals before hashing (step 3)
    model_id: str  # "<name>@<revision>"


class TraceLink(Model):
    requirement_key: str
    criterion_id: str | None
    entity_id: str
    state: LinkState
    evidence_ids: list[str]
    reasons: list[str]
