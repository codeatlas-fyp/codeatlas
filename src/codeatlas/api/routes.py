"""Read-only JSON endpoints over the bundle store (spec §7.6). Every route is a GET."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from codeatlas.api.cases import CaseStore, CaseSummary, ReplayOutcome
from codeatlas.bundle.hashing import sha256_hex
from codeatlas.schema.integration import (
    GraphEdge,
    GraphNode,
    GraphSnapshot,
    GraphView,
    SemanticRun,
    SemanticRunCandidate,
)


def get_store(request: Request) -> CaseStore:
    store: CaseStore = request.app.state.store
    return store


Store = Annotated[CaseStore, Depends(get_store)]

router = APIRouter(prefix="/api")


@router.get("/cases")
def list_cases(store: Store) -> list[CaseSummary]:
    return store.list_cases()


@router.get("/cases/{bundle_hash}")
def get_case(bundle_hash: str, store: Store) -> dict[str, Any]:
    case = store.get(bundle_hash)
    return {
        "summary": case.summary.model_dump(mode="json"),
        "change": case.bundle.change.model_dump(mode="json"),
        "verdict": case.verdict.model_dump(mode="json"),
        "collection_notes": case.bundle.collection_notes,
    }


@router.get("/cases/{bundle_hash}/requirements/{key}")
def get_requirement(bundle_hash: str, key: str, store: Store) -> dict[str, Any]:
    bundle = store.requirement(bundle_hash, key).bundle
    return {
        "key": key,
        "versions": [v.model_dump(mode="json") for v in bundle.versions if v.key == key],
        "change_events": [e.model_dump(mode="json") for e in bundle.change_events if e.key == key],
        "lifecycle": [f.model_dump(mode="json") for f in bundle.lifecycle if f.key == key],
    }


@router.get("/cases/{bundle_hash}/links")
def get_links(bundle_hash: str, store: Store) -> dict[str, Any]:
    case, links = store.links(bundle_hash)
    by_criterion: dict[str, list[dict[str, Any]]] = {}
    for candidate in sorted(case.bundle.candidates, key=lambda c: (c.criterion_id, c.fused_rank)):
        by_criterion.setdefault(candidate.criterion_id, []).append(
            candidate.model_dump(mode="json")
        )
    return {
        "links": [link.model_dump(mode="json") for link in links],
        "criteria": [c.model_dump(mode="json") for c in case.bundle.criteria],
        "candidates_by_criterion": by_criterion,
    }


@router.get("/cases/{bundle_hash}/graph", response_model=GraphView)
def get_graph(bundle_hash: str, store: Store) -> GraphView:
    bundle = store.get(bundle_hash).bundle
    return GraphView(
        status="incomplete" if bundle.entities or bundle.edges else "unavailable",
        snapshot=GraphSnapshot(
            repo=bundle.change.repo,
            base_sha=bundle.change.base_sha,
            head_sha=bundle.change.head_sha,
        ),
        nodes=[
            GraphNode(
                id=entity.entity_id,
                kind=entity.kind,
                entity_id=entity.entity_id,
                file=entity.file,
                start_line=entity.start_line,
                end_line=entity.end_line,
            )
            for entity in bundle.entities
        ],
        edges=[
            GraphEdge(
                id=sha256_hex(f"{edge.kind}\0{edge.src}\0{edge.dst}".encode()),
                src=edge.src,
                dst=edge.dst,
                kind=edge.kind,
                confident=edge.confident,
            )
            for edge in bundle.edges
        ],
        limitations=[
            "Legacy graph data lacks per-entity revisions and edge resolution provenance.",
            "Static edges do not prove execution.",
        ],
    )


@router.get("/cases/{bundle_hash}/semantic-runs", response_model=list[SemanticRun])
def get_semantic_runs(bundle_hash: str, store: Store) -> list[SemanticRun]:
    bundle = store.get(bundle_hash).bundle
    return [
        SemanticRun(
            criterion_id=criterion.criterion_id,
            status="unavailable",
            reason="Legacy rankings lack a recorded gate decision and candidate universe.",
            candidates=[
                SemanticRunCandidate(
                    entity_id=candidate.entity_id,
                    rank=candidate.fused_rank,
                    raw_score=candidate.fused_score,
                    score_kind="fused_score",
                )
                for candidate in sorted(bundle.candidates, key=lambda item: item.fused_rank)
                if candidate.criterion_id == criterion.criterion_id
            ],
        )
        for criterion in bundle.criteria
    ]


@router.get("/cases/{bundle_hash}/replay")
def get_replay(bundle_hash: str, store: Store) -> ReplayOutcome:
    return store.replay(bundle_hash)
