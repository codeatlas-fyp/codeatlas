"""Read-only JSON endpoints over the bundle store (spec §7.6). Every route is a GET."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from codeatlas.api.cases import CaseStore, CaseSummary, ReplayOutcome


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


@router.get("/cases/{bundle_hash}/replay")
def get_replay(bundle_hash: str, store: Store) -> ReplayOutcome:
    return store.replay(bundle_hash)
