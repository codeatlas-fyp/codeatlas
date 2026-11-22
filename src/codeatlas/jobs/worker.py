"""Long-running worker process for durable background evaluations."""

import io
import logging
import threading
import time
import uuid

from sqlalchemy import create_engine

from codeatlas.api.settings import WebSettings
from codeatlas.bundle.store import BundleStore
from codeatlas.db import make_session_factory
from codeatlas.errors import CollectionError
from codeatlas.jobs.contract import SubmissionContract
from codeatlas.jobs.service import JobService
from codeatlas.jobs.sources import job_sources
from codeatlas.logging import EventLog
from codeatlas.pipeline import CollectRequest, PullRequestTarget, collect_and_evaluate

LOGGER = logging.getLogger("codeatlas.worker")


def run_worker(settings: WebSettings | None = None, *, once: bool = False) -> None:
    settings = settings or WebSettings()
    settings.validate_runtime()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = make_session_factory(engine)
    jobs = JobService(factory, lease_seconds=settings.job_lease_seconds)
    try:
        while True:
            # A fresh token per claim fences an earlier execution even when two
            # processes share the same configured worker name.
            lease_token = uuid.uuid4().hex
            job = jobs.claim(lease_token)
            if job is None:
                if once:
                    return
                time.sleep(1)
                continue
            _execute(job, settings, factory, jobs, lease_token)
            if once:
                return
    finally:
        engine.dispose()


def _execute(job, settings: WebSettings, factory, jobs: JobService, lease_token: str) -> None:
    stop_heartbeat = threading.Event()

    def heartbeat() -> None:
        interval = max(1, settings.job_lease_seconds // 3)
        while not stop_heartbeat.wait(interval):
            try:
                if not jobs.heartbeat(job.id, lease_token):
                    LOGGER.warning("worker lease lost for evaluation %s", job.id)
                    return
            except Exception:
                LOGGER.warning("could not refresh worker lease for evaluation %s", job.id)

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True, name=f"heartbeat-{job.id}")
    heartbeat_thread.start()

    def persist_stage(stage: str, status: str) -> None:
        jobs.stage(job.id, stage, status, worker_id=lease_token)

    log = EventLog(uuid.uuid4().hex, stream=io.StringIO(), stage_callback=persist_stage)
    try:
        if not job.submission_contract:
            raise ValueError("legacy job has no frozen submission contract")
        contract = SubmissionContract.model_validate_json(job.submission_contract)
        with job_sources(job, settings, factory, contract) as (jira, sources):
            request = CollectRequest(
                change=None,
                change_label=f"pr:{job.repository}#{job.pr_number}",
                policy=contract.policy,
                policy_source=contract.policy_source,
                require_unchanged_jira=True,
                identity_map=contract.identity_map,
                identity_map_source=contract.identity_map_source,
                run_id=log.run_id,
                collected_at=contract.cutoff,
                notes=[
                    f"explicit source mode: {contract.source_mode}",
                    "Submission cutoff is frozen; live source state may have changed "
                    "before collection.",
                ],
                target=PullRequestTarget(repo=job.repository, number=job.pr_number),
                bm25_only=contract.source_mode == "fixtures",
            )
            bundle_hash, verdict = collect_and_evaluate(
                request,
                jira,
                BundleStore(settings.bundle_dir),
                log,
                sources,
            )
        jobs.complete(
            job.id,
            bundle_hash,
            verdict.value,
            str(BundleStore(settings.bundle_dir).folder(bundle_hash)),
            lease_token,
        )
    except Exception as error:
        code = _failure_code(error)
        # Persist only a safe classification; provider payloads and stack traces stay private.
        jobs.fail(job.id, code, "Evaluation failed", lease_token)
        LOGGER.warning("evaluation %s failed (%s, %s)", job.id, code, type(error).__name__)
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)


def _failure_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(error, CollectionError):
        if error.source == "jira" and error.status in {401, 403}:
            return "jira_auth"
        if error.source == "github" and error.status in {401, 403}:
            return "github_auth"
        if error.status == 409:
            return "sha_mismatch"
    return "evaluation_failed"


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()
