"""Read-only JSON endpoints over the bundle store (spec §7.6). Every route is a GET."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request

from codeatlas.api.cases import CaseStore, CaseSummary, ReplayOutcome
from codeatlas.graph import build_graph_view
from codeatlas.schema.integration import (
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
    result: dict[str, Any] = {
        "key": key,
        "versions": [v.model_dump(mode="json") for v in bundle.versions if v.key == key],
        "change_events": [e.model_dump(mode="json") for e in bundle.change_events if e.key == key],
        "lifecycle": [f.model_dump(mode="json") for f in bundle.lifecycle if f.key == key],
    }
    if bundle.frozen_jira_cutoff and key in bundle.frozen_jira_cutoff:
        result["frozen_cutoff"] = bundle.frozen_jira_cutoff[key]
    return result


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
    case, links = store.links(bundle_hash)
    return build_graph_view(case.bundle, links)


@router.get("/cases/{bundle_hash}/semantic-runs", response_model=list[SemanticRun])
def get_semantic_runs(bundle_hash: str, store: Store) -> list[SemanticRun]:
    bundle = store.get(bundle_hash).bundle
    summary = bundle.semantic_run
    if summary is not None:
        gates = {gate.criterion_id: gate for gate in summary.gates}
        entities = {entity.entity_id: entity for entity in bundle.entities}
        rankers = [ranker.model_dump(mode="json") for ranker in summary.rankers]
        active = [ranker for ranker in summary.rankers if ranker.state != "unavailable"]
        only_bm25 = bool(active) and all(ranker.name == "bm25" for ranker in active)
        results = []
        for criterion in bundle.criteria:
            gate = gates.get(criterion.criterion_id)
            candidates = [
                candidate
                for candidate in sorted(bundle.candidates, key=lambda item: item.fused_rank)
                if candidate.criterion_id == criterion.criterion_id
            ]
            statuses: dict[
                str, Literal["accepted", "review", "abstained", "unavailable", "fallback"]
            ] = {
                "accept": "accepted",
                "review": "fallback" if only_bm25 else "review",
                "abstain": "abstained",
                "unavailable": "unavailable",
            }
            state = "unavailable" if gate is None else statuses[gate.gate]
            results.append(
                SemanticRun(
                    criterion_id=criterion.criterion_id,
                    status=state,
                    reason=gate.reason if gate else "No gate outcome was recorded.",
                    source_revision=summary.pinned_sha,
                    candidate_universe_hash=summary.corpus_hash,
                    model_id=candidates[0].model_id if candidates else None,
                    representation_version=summary.chunker_version,
                    gate_profile=summary.calibration_id,
                    rankers=rankers,
                    candidates=[
                        SemanticRunCandidate(
                            entity_id=candidate.entity_id,
                            file=entities[candidate.entity_id].file
                            if candidate.entity_id in entities
                            else None,
                            start_line=entities[candidate.entity_id].start_line
                            if candidate.entity_id in entities
                            else None,
                            end_line=entities[candidate.entity_id].end_line
                            if candidate.entity_id in entities
                            else None,
                            revision=summary.pinned_sha,
                            rank=candidate.fused_rank,
                            raw_score=candidate.fused_score,
                            score_kind="rrf_fused_score",
                            accepted=None,
                        )
                        for candidate in candidates
                    ],
                )
            )
        return results
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
