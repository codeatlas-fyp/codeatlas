"""Settings from the environment, and loading of policy, identity map and change files.

Spec §15 "Configuration"; step-8 spec. Any invalid input is a `ConfigError` (exit code 4) that
names the file or the missing setting.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from codeatlas.errors import ConfigError
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    CasePolicy,
    IdentityMap,
    Policy,
    SemanticPolicy,
)

# Spec §7.5 defaults, used (and recorded as a collection note) when no policy file is given.
DEFAULT_POLICY = Policy(
    version=1,
    workitem_key_pattern="[A-Z]+-\\d+",
    requirement_fields=["summary", "description", "acceptance_criteria"],
    governance_paths=[
        "CODEOWNERS",
        ".github/CODEOWNERS",
        "docs/CODEOWNERS",
        "docs/adr/**",
        ".codeatlas/**",
        ".github/workflows/**",
        ".pre-commit-config.yaml",
    ],
    approval=ApprovalPolicy(status="Approved", approver_group="sbx-requirement-approvers"),
    priority_authority=AuthorityPolicy(group="sbx-priority-authority"),
    cases={
        "C3": CasePolicy(severity="block"),
        "C5": CasePolicy(severity="block"),
        "C7": CasePolicy(required_when_label="requires-test-evidence"),
    },
    semantic=SemanticPolicy(top_k=5),
)


class Settings(BaseSettings):
    """`CODEATLAS_*` environment variables, also read from `.env` in the working directory."""

    model_config = SettingsConfigDict(env_prefix="CODEATLAS_", env_file=".env", extra="ignore")

    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_token: SecretStr | None = None
    jira_max_retries: int = 3
    github_token: SecretStr | None = None
    bundle_dir: Path = Path("bundles")
    log_level: str = "INFO"
    embedding_model: str | None = None

    def jira(self) -> tuple[str, str, str]:
        """Base URL, email and token for a live Jira run; a missing one is a ConfigError."""
        values = {
            "CODEATLAS_JIRA_BASE_URL": self.jira_base_url,
            "CODEATLAS_JIRA_EMAIL": self.jira_email,
            "CODEATLAS_JIRA_TOKEN": self.jira_token.get_secret_value() if self.jira_token else None,
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError(f"missing setting(s) for a live Jira run: {', '.join(missing)}")
        return (
            str(values["CODEATLAS_JIRA_BASE_URL"]),
            str(values["CODEATLAS_JIRA_EMAIL"]),
            str(values["CODEATLAS_JIRA_TOKEN"]),
        )


class ChangeWorkItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    found_in: Literal["branch", "pr_title", "pr_body", "commit"]
    commit_sha: str | None = None


class ChangeCommit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sha: str
    author_email: str
    author_login: str | None = None
    committed_at: datetime
    message: str


class ChangeFile(BaseModel):
    """The change under evaluation, standing in for the GitHub and git collectors (step 9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    pr_number: int
    base_sha: str
    head_sha: str
    merge_base_sha: str
    work_items: list[ChangeWorkItem]
    commits: list[ChangeCommit]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(f"{path} not found") from error


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(_read(path))
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error


def load_policy(path: Path | None) -> tuple[Policy, list[str]]:
    """The policy and any collection notes about it (spec §5 stage 2)."""
    if path is None:
        return DEFAULT_POLICY, ["no policy file: built-in default policy used"]
    try:
        return Policy.model_validate(_yaml(path)), []
    except ValidationError as error:
        raise ConfigError(f"{path} is not a valid policy: {error}") from error


def load_identity_map(path: Path | None) -> IdentityMap:
    if path is None:
        return IdentityMap(people=[])
    try:
        return IdentityMap.model_validate(_yaml(path))
    except ValidationError as error:
        raise ConfigError(f"{path} is not a valid identity map: {error}") from error


def load_change(path: Path) -> ChangeFile:
    try:
        return ChangeFile.model_validate(json.loads(_read(path)))
    except (ValueError, ValidationError) as error:
        raise ConfigError(f"{path} is not a valid change file: {error}") from error
