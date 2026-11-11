"""Transactional job creation, claiming, progress, recovery, and result indexing."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from codeatlas.db_models import (
    EvaluationBundle,
    EvaluationJob,
    EvaluationStageEvent,
    ProjectConfig,
)

STAGES = (
    "queued",
    "resolve_change",
    "collect_requirements",
    "lifecycle",
    "diff",
    "governance",
    "code",
    "criteria",
    "semantic",
    "freeze",
    "store",
    "evaluate",
    "complete",
)


class JobService:
    def __init__(self, factory: sessionmaker[Session], *, lease_seconds: int = 120):
        self.factory = factory
        self.lease_seconds = lease_seconds

    def submit(
        self,
        project: ProjectConfig,
        pr_number: int,
        idempotency_key: str | None = None,
        base_sha: str | None = None,
        head_sha: str | None = None,
    ) -> tuple[EvaluationJob, bool]:
        active_key = f"{project.id}:{project.github_repository}:{pr_number}"
        with self.factory() as session:
            if idempotency_key:
                existing = session.scalar(
                    select(EvaluationJob).where(EvaluationJob.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    return existing, False
            active = session.scalar(
                select(EvaluationJob).where(EvaluationJob.active_key == active_key)
            )
            if active is not None:
                return active, False
            job = EvaluationJob(
                id=str(uuid4()),
                project_id=project.id,
                repository=project.github_repository,
                pr_number=pr_number,
                base_sha=base_sha,
                head_sha=head_sha,
                active_key=active_key,
                idempotency_key=idempotency_key,
                status="queued",
                stage="queued",
                attempt=0,
            )
            session.add(job)
            session.add(
                EvaluationStageEvent(
                    job_id=job.id,
                    stage="queued",
                    status="queued",
                    message="Evaluation queued",
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(EvaluationJob).where(EvaluationJob.active_key == active_key)
                )
                if existing is None and idempotency_key:
                    existing = session.scalar(
                        select(EvaluationJob).where(
                            EvaluationJob.idempotency_key == idempotency_key
                        )
                    )
                if existing is None:
                    raise
                return existing, False
            session.refresh(job)
            return job, True

    def claim(self, worker_id: str, max_attempts: int = 3) -> EvaluationJob | None:
        now = datetime.now(UTC)
        with self.factory() as session, session.begin():
            expired = session.scalars(
                select(EvaluationJob).where(
                    EvaluationJob.status == "running",
                    EvaluationJob.lease_expires_at < now,
                )
            ).all()
            for old in expired:
                if old.attempt >= max_attempts:
                    self._terminal(
                        session, old, "failed", "worker_lost", "Worker lease expired repeatedly."
                    )
                else:
                    old.status = "queued"
                    old.stage = "queued"
                    old.lease_owner = None
                    old.lease_expires_at = None
                    self._event(
                        session,
                        old.id,
                        "queued",
                        "recovered",
                        "Recovered after worker lease expiry",
                    )

            statement = (
                select(EvaluationJob)
                .where(EvaluationJob.status == "queued")
                .order_by(EvaluationJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = session.scalar(statement)
            if job is None:
                return None
            job.status = "running"
            job.stage = "resolve_change"
            job.attempt += 1
            job.lease_owner = worker_id
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.started_at = job.started_at or now
            self._event(session, job.id, "resolve_change", "started", "Preparing source evidence")
            session.flush()
            session.expunge(job)
            return job

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        now = datetime.now(UTC)
        with self.factory() as session, session.begin():
            job = session.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id))
            if job is None or job.status != "running" or job.lease_owner != worker_id:
                return False
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            return True

    def stage(
        self,
        job_id: str,
        stage: str,
        status: str,
        message: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        if stage not in STAGES:
            stage = "evaluate"
        safe_status = (
            status if status in {"started", "completed", "failed", "recovered"} else "completed"
        )
        with self.factory() as session, session.begin():
            job = session.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id))
            if (
                job is None
                or job.status != "running"
                or (worker_id and job.lease_owner != worker_id)
            ):
                return
            job.stage = stage
            self._event(
                session,
                job.id,
                stage,
                safe_status,
                message or _stage_message(stage, safe_status),
            )

    def complete(
        self, job_id: str, bundle_hash: str, verdict: str, bundle_path: str, worker_id: str
    ) -> None:
        with self.factory() as session, session.begin():
            job = session.scalar(
                select(EvaluationJob).where(EvaluationJob.id == job_id).with_for_update()
            )
            if job is None or job.status != "running" or job.lease_owner != worker_id:
                raise RuntimeError("evaluation job is no longer owned by this worker")
            session.add(
                EvaluationBundle(
                    evaluation_id=job.id,
                    bundle_path=bundle_path,
                    bundle_hash=bundle_hash,
                    verdict=verdict,
                )
            )
            self._terminal(session, job, "succeeded", None, None)
            job.stage = "complete"
            self._event(session, job.id, "complete", "completed", "Evaluation completed")

    def fail(self, job_id: str, code: str, message: str, worker_id: str | None = None) -> None:
        safe_message = _safe_failure_message(code)
        with self.factory() as session, session.begin():
            job = session.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id))
            if (
                job is None
                or job.status != "running"
                or (worker_id and job.lease_owner != worker_id)
            ):
                return
            self._terminal(session, job, "failed", code[:64], safe_message)

    def get(self, job_id: str) -> dict[str, object] | None:
        with self.factory() as session:
            job = session.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id))
            if job is None:
                return None
            events = session.scalars(
                select(EvaluationStageEvent)
                .where(EvaluationStageEvent.job_id == job_id)
                .order_by(EvaluationStageEvent.id)
            ).all()
            bundle = session.scalar(
                select(EvaluationBundle).where(EvaluationBundle.evaluation_id == job_id)
            )
            return _job_public(job, events, bundle)

    def history(self, limit: int = 100) -> list[dict[str, object]]:
        with self.factory() as session:
            jobs = session.scalars(
                select(EvaluationJob).order_by(EvaluationJob.created_at.desc()).limit(limit)
            ).all()
            return [self.get(job.id) or {} for job in jobs]

    @staticmethod
    def _event(session: Session, job_id: str, stage: str, status: str, message: str) -> None:
        now = datetime.now(UTC)
        session.add(
            EvaluationStageEvent(
                job_id=job_id,
                stage=stage,
                status=status,
                message=message[:300],
                at=now,
                started_at=now if status == "started" else None,
                ended_at=now if status in {"completed", "failed"} else None,
            )
        )

    def _terminal(
        self,
        session: Session,
        job: EvaluationJob,
        status: str,
        error_code: str | None,
        message: str | None,
    ) -> None:
        job.status = status
        job.completed_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        job.active_key = None
        job.safe_error_code = error_code
        job.safe_error_message = message
        if status == "failed":
            self._event(session, job.id, job.stage, "failed", message or "Evaluation failed")


def _stage_message(stage: str, status: str) -> str:
    labels = {
        "resolve_change": "Fetching pull request and preparing repository",
        "collect_requirements": "Collecting linked Jira requirements and history",
        "lifecycle": "Reconstructing requirement lifecycle",
        "diff": "Reading pull request changes",
        "governance": "Analyzing repository governance",
        "code": "Analyzing changed code",
        "criteria": "Extracting acceptance criteria",
        "semantic": "Matching criteria to code",
        "freeze": "Freezing evidence bundle",
        "store": "Storing evidence bundle",
        "evaluate": "Evaluating evidence and rules",
    }
    return f"{labels.get(stage, 'Evaluation stage')} {status}."


def _safe_failure_message(code: str) -> str:
    messages = {
        "jira_auth": "Jira rejected the saved credentials. Reconnect Jira and retry.",
        "github_auth": "GitHub rejected the saved token. Reconnect GitHub and retry.",
        "repo_fetch": "The repository could not be prepared. Check repository access and retry.",
        "sha_mismatch": (
            "The pull request changed while it was being prepared. Refresh the PR list and retry."
        ),
        "missing_ref": (
            "A required pull request commit is unavailable. Refresh the PR list and retry."
        ),
        "worker_lost": "The worker stopped repeatedly before finishing this evaluation.",
    }
    return messages.get(code, "Evaluation failed. Check the connection and retry.")


def _job_public(
    job: EvaluationJob, events: list[EvaluationStageEvent], bundle: EvaluationBundle | None
) -> dict[str, object]:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "repository": job.repository,
        "pull_request_number": job.pr_number,
        "base_sha": job.base_sha,
        "head_sha": job.head_sha,
        "status": job.status,
        "stage": job.stage,
        "attempt": job.attempt,
        "safe_error_code": job.safe_error_code,
        "error": job.safe_error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "events": [
            {
                "id": event.id,
                "stage": event.stage,
                "status": event.status,
                "message": event.message,
                "at": event.at.isoformat(),
                "started_at": event.started_at.isoformat() if event.started_at else None,
                "ended_at": event.ended_at.isoformat() if event.ended_at else None,
            }
            for event in events
        ],
        "bundle_hash": bundle.bundle_hash if bundle else None,
        "verdict": bundle.verdict if bundle else None,
    }
