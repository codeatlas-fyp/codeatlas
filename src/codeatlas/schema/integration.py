"""Application-facing contracts for separately produced graph and semantic exports."""

from typing import Literal, Self

from pydantic import Field, model_validator

from codeatlas.schema.types import Model


class GraphSnapshot(Model):
    repo: str
    base_sha: str | None
    head_sha: str | None


class GraphNode(Model):
    id: str
    kind: str
    entity_id: str | None = None
    file: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    revision: str | None = None


class GraphEdge(Model):
    id: str
    src: str
    dst: str
    kind: str
    confident: bool | None = None
    resolution_status: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class GraphView(Model):
    schema_version: Literal["graph-view-v1"] = "graph-view-v1"
    status: Literal["available", "incomplete", "unavailable"]
    snapshot: GraphSnapshot
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class SemanticRunCandidate(Model):
    entity_id: str
    file: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    revision: str | None = None
    rank: int = Field(ge=1)
    raw_score: float | None = None
    score_kind: str | None = None
    accepted: bool | None = None


class SemanticRun(Model):
    schema_version: Literal["semantic-run-v1"] = "semantic-run-v1"
    criterion_id: str
    source_revision: str | None = None
    candidate_universe_hash: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    representation_version: str | None = None
    status: Literal["accepted", "abstained", "unavailable", "fallback"]
    gate_profile: str | None = None
    reason: str
    candidates: list[SemanticRunCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        accepted = any(candidate.accepted is True for candidate in self.candidates)
        if self.status == "accepted":
            if not accepted:
                raise ValueError("accepted runs must contain an accepted candidate")
            if not all(
                (
                    self.source_revision,
                    self.candidate_universe_hash,
                    self.model_id,
                    self.model_revision,
                    self.representation_version,
                    self.gate_profile,
                )
            ):
                raise ValueError("accepted runs require source, model, corpus and gate provenance")
        elif self.status in {"abstained", "unavailable"} and accepted:
            raise ValueError("abstained or unavailable runs cannot contain accepted candidates")
        return self
