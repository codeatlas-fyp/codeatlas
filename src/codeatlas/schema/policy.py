"""Policy and identity map, typed from the YAML in spec §7.5 as amended by step-2 spec S1.

Approvers and priority authority may be given as a Jira group, as explicit account ids, or both:
group membership may not be readable on every Jira plan.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from codeatlas.schema.types import CaseId, Model


class ApprovalPolicy(Model):
    status: str  # "Approved" in the SBX sandbox
    approver_group: str | None = None
    approver_account_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_approvers(self) -> Self:
        if self.approver_group is None and not self.approver_account_ids:
            raise ValueError("approval needs a group or at least one account id")
        return self


class AuthorityPolicy(Model):
    group: str | None = None
    account_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_members(self) -> Self:
        if self.group is None and not self.account_ids:
            raise ValueError("priority authority needs a group or at least one account id")
        return self


class CasePolicy(Model):
    severity: Literal["block", "review"] | None = None
    required_when_label: str | None = None


class SemanticPolicy(Model):
    top_k: int


class Policy(Model):
    """`.codeatlas/policy.yaml`, read at the base commit of the evaluated repository."""

    version: int
    workitem_key_pattern: str
    requirement_fields: list[str]
    governance_paths: list[str]
    approval: ApprovalPolicy
    priority_authority: AuthorityPolicy
    cases: dict[CaseId, CasePolicy]
    semantic: SemanticPolicy


class IdentityEntry(Model):
    jira_account_id: str
    github_login: str | None
    git_emails: list[str]


class IdentityMap(Model):
    """`.codeatlas/identity_map.yaml`: git emails and GitHub logins to Jira account ids."""

    people: list[IdentityEntry]
