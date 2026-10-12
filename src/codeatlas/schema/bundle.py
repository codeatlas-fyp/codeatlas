"""The evidence bundle and the verdict over it (spec §7.1, §7.4).

Additions approved in docs/specs/step-2-schema.md (S3): `collected_at`, `run_id`, `policy`,
`identity_map` and `model_file_hashes`. The bundle holds no `bundle_hash`: it is
content-addressed, and the hash travels in `Verdict.bundle_hash`.
"""

from typing import Literal

from codeatlas.schema.evidence import (
    ChangedEntity,
    ChangeEvent,
    ChangeRef,
    CodeEdge,
    CodeEntity,
    Commit,
    Criterion,
    GovernanceChange,
    GovernanceSnapshot,
    LifecycleFacts,
    RequirementVersion,
    ResolvedPerson,
    Review,
    SemanticCandidate,
    WorkItemRef,
)
from codeatlas.schema.policy import IdentityMap, Policy
from codeatlas.schema.types import CaseId, Model, Outcome, UtcDatetime, VerdictValue


class CheckResult(Model):
    case: CaseId
    outcome: Outcome
    severity: Literal["block", "review", "none"]
    required: bool
    message: str
    evidence_ids: list[str]


class Verdict(Model):
    value: VerdictValue
    results: list[CheckResult]
    ruleset_version: str
    bundle_hash: str
    verdict_hash: str


class EvidenceBundle(Model):
    schema_version: Literal["0"]
    change: ChangeRef
    codeatlas_version: str
    collected_at: UtcDatetime  # rules use this, never the clock
    run_id: str
    extractor_versions: dict[str, str]
    model_ids: list[str]
    model_file_hashes: dict[str, str]  # model_id -> sha256 of the model file
    policy: Policy  # copied in so replay never reads live files
    identity_map: IdentityMap
    work_items: list[WorkItemRef]
    change_events: list[ChangeEvent]
    versions: list[RequirementVersion]
    lifecycle: list[LifecycleFacts]
    commits: list[Commit]
    reviews: list[Review]
    people: list[ResolvedPerson]
    governance: GovernanceSnapshot
    governance_changes: list[GovernanceChange]
    entities: list[CodeEntity]
    changed: list[ChangedEntity]
    edges: list[CodeEdge]
    criteria: list[Criterion]
    candidates: list[SemanticCandidate]
    collection_notes: list[str]  # e.g. "work item SBX-99 not found"
