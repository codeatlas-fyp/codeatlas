"""Evaluation submission, persisted job progress/history, and result access."""

import math
import re
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from codeatlas.api.cases import CaseStore
from codeatlas.api.comparison import compare_cases
from codeatlas.api.connections import ServiceDep, SessionDep
from codeatlas.api.settings import WebSettings
from codeatlas.collect.github.client import GitHubClient, GitHubClientConfig
from codeatlas.db_models import Connection, EvaluationBundle, EvaluationJob, ProjectConfig
from codeatlas.integrations.fixtures import fixture_pr
from codeatlas.jobs.service import JobService, SubmissionConflict

router = APIRouter(prefix="/api")
_SHA = re.compile(r"[0-9a-fA-F]{40,64}\Z")


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pull_request_number: int = Field(gt=0, le=2_147_483_647)


class EvaluationAccepted(BaseModel):
    id: str
    status: str
    stage: str
    created_at: str
    poll_url: str
    reused: bool


@router.post(
    "/evaluations", status_code=status.HTTP_202_ACCEPTED, response_model=EvaluationAccepted
)
def submit_evaluation(
    body: EvaluationRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    service: ServiceDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EvaluationAccepted:
    if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
        raise HTTPException(
            422,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must contain 1 to 200 characters.",
            },
        )
    project = session.scalar(select(ProjectConfig).order_by(ProjectConfig.created_at).limit(1))
    if project is None:
        raise HTTPException(
            409,
            detail={
                "code": "project_not_configured",
                "message": (
                    "Connect accounts and select a Jira project and GitHub repository first."
                ),
            },
        )
    base_sha: str | None = None
    head_sha: str | None = None
    settings: WebSettings = request.app.state.settings
    if settings.source_mode == "fixtures":
        try:
            pr = fixture_pr(settings, body.pull_request_number)
        except (OSError, ValueError) as error:
            raise HTTPException(
                404,
                detail={
                    "code": "pull_request_not_found",
                    "message": "No fixture pull request with that number.",
                },
            ) from error
    else:
        connection = session.scalar(select(Connection).where(Connection.provider == "github"))
        if connection is None:
            raise HTTPException(
                409,
                detail={"code": "github_connection_required", "message": "Connect GitHub first."},
            )
        try:
            _, token = service.credentials(session, "github")
        except Exception as error:
            raise HTTPException(
                409,
                detail={
                    "code": "github_connection_required",
                    "message": "Reconnect GitHub before evaluating.",
                },
            ) from error
        client = GitHubClient(GitHubClientConfig(token=token))
        try:
            pr = client.pull_request(project.github_repository, body.pull_request_number)
        except Exception as error:
            status_code = getattr(error, "status", None)
            if status_code == 404:
                raise HTTPException(
                    404,
                    detail={
                        "code": "pull_request_not_found",
                        "message": "Pull request was not found or is not accessible.",
                    },
                ) from error
            raise HTTPException(
                502,
                detail={
                    "code": "github_unavailable",
                    "message": (
                        "Could not retrieve the pull request. "
                        "Check the GitHub connection and retry."
                    ),
                },
            ) from error
        finally:
            client.close()
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base_sha = base.get("sha") if isinstance(base.get("sha"), str) else None
    head_sha = head.get("sha") if isinstance(head.get("sha"), str) else None
    if not base_sha or not head_sha or not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(head_sha):
        if settings.source_mode == "fixtures":
            # Recorded fixture placeholders are not Git object IDs.
            base_sha = head_sha = None
        else:
            raise HTTPException(
                502,
                detail={
                    "code": "invalid_pull_request_metadata",
                    "message": "GitHub returned incomplete pull request commit metadata.",
                },
            )
    if pr.get("state") not in {None, "open"} or pr.get("merged") is True:
        raise HTTPException(
            422,
            detail={
                "code": "pull_request_not_open",
                "message": "Only open, unmerged pull requests can be evaluated.",
            },
        )

    jobs: JobService = request.app.state.jobs
    try:
        job, created = jobs.submit(
            project,
            body.pull_request_number,
            idempotency_key.strip() if idempotency_key else None,
            base_sha,
            head_sha,
            source_mode=settings.source_mode,
            actor_scope=settings.web_user or "local-single-user",
        )
    except SubmissionConflict as error:
        raise HTTPException(
            409, detail={"code": "idempotency_conflict", "message": str(error)}
        ) from error
    response.headers["Location"] = f"/api/evaluations/{job.id}"
    return EvaluationAccepted(
        id=job.id,
        status=job.status,
        stage=job.stage,
        created_at=job.created_at.isoformat(),
        poll_url=f"/api/evaluations/{job.id}",
        reused=not created,
    )


@router.get("/evaluations")
def evaluation_history(request: Request) -> list[dict[str, object]]:
    return request.app.state.jobs.history()


@router.get("/evaluations/compare")
def compare_evaluations(before: str, after: str, request: Request) -> dict[str, object]:
    store = CaseStore(request.app.state.settings.bundle_dir)
    cases = []
    with request.app.state.session_factory() as session:
        for evaluation_id in (before, after):
            job = session.get(EvaluationJob, evaluation_id)
            if job is None:
                raise HTTPException(404, detail={"code": "evaluation_not_found"})
            bundle = session.scalar(
                select(EvaluationBundle).where(EvaluationBundle.evaluation_id == evaluation_id)
            )
            if job.status != "succeeded" or bundle is None:
                raise HTTPException(409, detail={"code": "evaluation_incomplete"})
            cases.append(store.get(bundle.bundle_hash))
    return compare_cases(cases[0], cases[1], before, after)


@router.get("/evaluations/{evaluation_id}")
def evaluation_status(evaluation_id: str, request: Request) -> dict[str, object]:
    result = request.app.state.jobs.get(evaluation_id)
    if result is None:
        raise HTTPException(
            404, detail={"code": "evaluation_not_found", "message": "Evaluation was not found."}
        )
    return result


@router.get("/evaluations/{evaluation_id}/case")
def evaluation_case(evaluation_id: str, request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        job = session.get(EvaluationJob, evaluation_id)
        bundle_ref = session.scalar(
            select(EvaluationBundle).where(EvaluationBundle.evaluation_id == evaluation_id)
        )
    if job is None:
        raise HTTPException(
            404, detail={"code": "evaluation_not_found", "message": "Evaluation was not found."}
        )
    if job.status != "succeeded" or bundle_ref is None:
        raise HTTPException(
            409,
            detail={
                "code": "evaluation_incomplete",
                "message": "The evaluation has no completed evidence bundle yet.",
            },
        )
    case = CaseStore(request.app.state.settings.bundle_dir).get(bundle_ref.bundle_hash)
    bundle_data = case.bundle.model_dump(mode="json")
    semantic_run = bundle_data.get("semantic_run")
    if isinstance(semantic_run, dict):
        for name in ("accept_threshold", "review_threshold"):
            value = semantic_run.get(name)
            if isinstance(value, float) and not math.isfinite(value):
                semantic_run[name] = str(value)
    return {
        "evaluation_id": evaluation_id,
        "bundle_hash": bundle_ref.bundle_hash,
        "case": case.summary.model_dump(mode="json"),
        "change": case.bundle.change.model_dump(mode="json"),
        "bundle": bundle_data,
        "verdict": case.verdict.model_dump(mode="json"),
        "collection_notes": case.bundle.collection_notes,
        "replay_url": f"/api/cases/{bundle_ref.bundle_hash}/replay",
    }


@router.get("/evaluations/{evaluation_id}/replay")
def evaluation_replay(evaluation_id: str, request: Request):
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        bundle_ref = session.scalar(
            select(EvaluationBundle).where(EvaluationBundle.evaluation_id == evaluation_id)
        )
    if bundle_ref is None:
        raise HTTPException(
            404,
            detail={
                "code": "evaluation_bundle_not_found",
                "message": "No completed bundle is linked to this evaluation.",
            },
        )
    return CaseStore(request.app.state.settings.bundle_dir).replay(bundle_ref.bundle_hash)
