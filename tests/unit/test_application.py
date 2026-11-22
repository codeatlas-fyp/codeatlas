"""Single-user application persistence, credential handling, and fixture end-to-end flow."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from codeatlas.api.app import create_app
from codeatlas.api.settings import WebSettings
from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.db import Base
from codeatlas.db_models import Connection, EvaluationJob, EvaluationStageEvent, ProjectConfig
from codeatlas.integrations.credentials import CredentialVault
from codeatlas.jobs import worker as worker_module
from codeatlas.jobs.service import JobService, SubmissionConflict
from codeatlas.jobs.worker import run_worker

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_worker_uses_fresh_lease_token_for_each_execution(tmp_path, monkeypatch):
    tokens = []

    class FakeJobs:
        def __init__(self, *args, **kwargs):
            pass

        def claim(self, token):
            tokens.append(token)
            return object()

    executions = []
    monkeypatch.setattr(worker_module, "JobService", FakeJobs)
    monkeypatch.setattr(
        worker_module,
        "_execute",
        lambda job, settings, factory, jobs, token: executions.append(token),
    )
    settings = _settings(tmp_path)
    run_worker(settings, once=True)
    run_worker(settings, once=True)
    assert len(set(tokens)) == 2
    assert executions == tokens


def _settings(tmp_path, *, mode="fixtures", database_url=None):
    return WebSettings(
        database_url=database_url or f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
        bundle_dir=tmp_path / "bundles",
        repo_cache_dir=tmp_path / "repo-cache",
        credential_key=Fernet.generate_key().decode(),
        source_mode=mode,
        jira_fixture_dir=FIXTURES / "jira",
        github_fixture_dir=FIXTURES / "github",
    )


def _app(tmp_path, *, mode="fixtures", database_url=None):
    application = create_app(_settings(tmp_path, mode=mode, database_url=database_url))
    Base.metadata.create_all(application.state.engine)
    return application


def test_credential_vault_encrypts_and_authenticates() -> None:
    key = Fernet.generate_key().decode()
    token = "ghp-secret-example"
    stored = CredentialVault(key).encrypt(token)
    assert token not in stored
    assert CredentialVault(key).decrypt(stored) == token
    with pytest.raises(ValueError, match="cannot be decrypted"):
        CredentialVault(Fernet.generate_key().decode()).decrypt(stored)


def test_browser_origin_is_checked_for_state_changes(tmp_path) -> None:
    app = _app(tmp_path)
    selection = {"jira_project": "SBX", "github_repository": "codeatlas-fyp/codeatlas-sandbox"}
    with TestClient(app) as client:
        rejected = client.put(
            "/api/project-config", json=selection, headers={"Origin": "https://attacker.example"}
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "cross_origin_rejected"
        assert client.get("/api/setup").json()["project"] is None
        assert (
            client.put(
                "/api/project-config", json=selection, headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/project-config", json=selection, headers={"Origin": "http://testserver"}
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("https://example.test", True),
        ("https://example.test:443", True),
        ("https://example.test:444", False),
        ("null", False),
        ("https://example.test/path", False),
        ("https://other.test", False),
    ],
)
def test_origin_comparison_handles_ports_and_invalid_origins(origin, expected) -> None:
    from codeatlas.api.app import _same_origin

    assert _same_origin(origin, "https://example.test/api/evaluations") is expected


@respx.mock
def test_live_credentials_validate_store_encrypted_and_never_return_tokens(tmp_path) -> None:
    app = _app(tmp_path, mode="live")
    respx.get("https://jira.example/rest/api/3/myself").mock(
        return_value=Response(200, json={"displayName": "Jira Demo"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=Response(200, json={"login": "demo-user"})
    )
    jira_token = "jira-token-do-not-return"
    github_token = "github-token-do-not-return"
    with TestClient(app) as client:
        jira = {
            "base_url": "https://jira.example",
            "email": "demo@example.test",
            "api_token": jira_token,
        }
        github = {"personal_access_token": github_token}
        assert client.post("/api/connections/jira/validate", json=jira).status_code == 200
        saved_jira = client.post("/api/connections/jira", json=jira)
        assert saved_jira.status_code == 201
        assert client.post("/api/connections/github/validate", json=github).status_code == 200
        saved_github = client.post("/api/connections/github", json=github)
        assert saved_github.status_code == 201
        body = saved_jira.text + saved_github.text + client.get("/api/setup").text
        assert jira_token not in body
        assert github_token not in body
    with app.state.session_factory() as session:
        rows = session.scalars(select(Connection)).all()
        stored = {row.provider: row.encrypted_credential for row in rows}
        assert stored["jira"] != jira_token
        assert stored["github"] != github_token
        vault = CredentialVault(app.state.settings.credential_key.get_secret_value())
        assert vault.decrypt(stored["jira"]) == jira_token
        assert vault.decrypt(stored["github"]) == github_token


@respx.mock
def test_invalid_live_github_token_returns_safe_validation_error(tmp_path) -> None:
    app = _app(tmp_path, mode="live")
    respx.get("https://api.github.com/user").mock(
        return_value=Response(401, json={"message": "token should not be returned"})
    )
    secret = "invalid-github-token"
    with TestClient(app) as client:
        response = client.post(
            "/api/connections/github/validate",
            json={"personal_access_token": secret},
        )
    assert response.status_code == 422
    assert "rejected" in response.text
    assert secret not in response.text
    assert "token should not be returned" not in response.text


def test_fixture_dashboard_evaluation_persists_history_bundle_and_replay(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    app = _app(tmp_path, database_url=settings.database_url)
    with TestClient(app) as client:
        assert client.get("/setup").status_code == 200
        assert "Fixture mode" in client.get("/setup").text
        assert client.get("/api/jira/projects").json()[0]["key"] == "SBX"
        assert client.get("/api/github/repositories").json()[0]["full_name"] == (
            "codeatlas-fyp/codeatlas-sandbox"
        )
        saved = client.put(
            "/api/project-config",
            json={
                "jira_project": "SBX",
                "github_repository": "codeatlas-fyp/codeatlas-sandbox",
            },
        )
        assert saved.status_code == 200
        prs = client.get("/api/github/repositories/codeatlas-fyp/codeatlas-sandbox/pull-requests")
        assert {item["number"] for item in prs.json()} == {1, 2}
        created = client.post(
            "/api/evaluations",
            json={"pull_request_number": 1},
            headers={"Idempotency-Key": "fixture-evaluation-1"},
        )
        assert created.status_code == 202
        job_id = created.json()["id"]
        duplicate = client.post(
            "/api/evaluations",
            json={"pull_request_number": 1},
            headers={"Idempotency-Key": "fixture-evaluation-1"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == job_id
        assert duplicate.json()["reused"] is True

    from codeatlas.jobs import service as service_module
    from codeatlas.jobs.contract import SubmissionContract

    with app.state.session_factory() as session:
        submitted = session.get(EvaluationJob, job_id)
        contract = SubmissionContract.model_validate_json(submitted.submission_contract)

    original_policy = service_module.DEFAULT_POLICY.model_dump(mode="json")
    monkeypatch.setattr(
        service_module,
        "DEFAULT_POLICY",
        service_module.DEFAULT_POLICY.model_copy(update={"workitem_key_pattern": "CHANGED"}),
    )
    run_worker(settings, once=True)

    with TestClient(app) as client:
        job = client.get(f"/api/evaluations/{job_id}").json()
        assert job["status"] == "succeeded"
        assert job["bundle_hash"]
        assert job["verdict"] in {"PASS", "FAIL", "REVIEW", "UNKNOWN"}
        assert any(event["status"] == "started" for event in job["events"])
        case = client.get(f"/api/evaluations/{job_id}/case")
        assert case.status_code == 200
        data = case.json()
        assert datetime.fromisoformat(data["bundle"]["collected_at"].replace("Z", "+00:00")) == (
            contract.cutoff
        )
        assert data["bundle"]["change"]["pr_number"] == 1
        assert data["bundle"]["pr_author_login"] == "user-01"
        assert data["bundle"]["policy_source"] == "built-in default at submission"
        assert canonical_bytes(data["bundle"]["policy"]) == canonical_bytes(original_policy)
        assert data["verdict"]["results"]
        assert client.get(data["replay_url"]).json()["match"] is True
        graph = client.get(f"/api/cases/{data['bundle_hash']}/graph")
        assert graph.status_code == 200
        assert graph.json()["schema_version"] == "graph-view-v1"
        runs = client.get(f"/api/cases/{data['bundle_hash']}/semantic-runs")
        assert runs.status_code == 200
        assert runs.json()
        assert all(run["status"] in {"fallback", "abstained"} for run in runs.json())
        assert all(
            any(r["name"] == "bm25" and r["state"] == "ok" for r in run["rankers"])
            for run in runs.json()
        )
        assert client.get("/api/evaluations").json()[0]["id"] == job_id

    restarted = _app(tmp_path, database_url=settings.database_url)
    with TestClient(restarted) as client:
        history = client.get("/api/evaluations").json()
        assert history[0]["id"] == job_id
        assert history[0]["status"] == "succeeded"


def test_compare_two_completed_fixture_evaluations(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = _app(tmp_path, database_url=settings.database_url)
    _configured_project(app)
    ids = []
    for number in (1, 2):
        with TestClient(app) as client:
            response = client.post("/api/evaluations", json={"pull_request_number": number})
            assert response.status_code == 202
            ids.append(response.json()["id"])
        run_worker(settings, once=True)
    with TestClient(app) as client:
        response = client.get(
            "/api/evaluations/compare", params={"before": ids[0], "after": ids[1]}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["before"]["evaluation_id"] == ids[0]
        assert result["after"]["evaluation_id"] == ids[1]
        assert result["before"]["source_mode"] == "fixtures"
        assert result["after"]["source_mode"] == "fixtures"
        assert result["directly_comparable"] is False
        assert "Different repository or pull request." in result["limitations"]
        assert "cannot be compared" in result["semantic_note"]
        assert any(finding["case"] == "C5" for finding in result["findings"])
        assert (
            client.get(
                "/api/evaluations/compare", params={"before": ids[0], "after": "missing"}
            ).status_code
            == 404
        )


def test_job_claim_uses_skip_locked_and_recovers_expired_lease(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        jira = Connection(
            provider="jira", display_name="Jira", configuration="{}", encrypted_credential="x"
        )
        github = Connection(
            provider="github", display_name="GitHub", configuration="{}", encrypted_credential="x"
        )
        session.add_all([jira, github])
        session.flush()
        project = ProjectConfig(
            name="test",
            jira_project="SBX",
            github_repository="owner/repo",
            jira_connection_id=jira.id,
            github_connection_id=github.id,
        )
        session.add(project)
        session.flush()
        job = EvaluationJob(
            project_id=project.id,
            repository="owner/repo",
            pr_number=1,
            status="running",
            stage="code",
            active_key="active",
            attempt=1,
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(job)
        session.flush()
        job_id = job.id

    statement = select(EvaluationJob).with_for_update(skip_locked=True)
    assert "FOR UPDATE SKIP LOCKED" in str(statement.compile(dialect=postgresql.dialect()))
    jobs = JobService(factory)
    assert not jobs.heartbeat(job_id, "dead-worker")
    jobs.stage(job_id, "semantic", "completed", worker_id="dead-worker")
    with pytest.raises(RuntimeError, match="no longer owned"):
        jobs.complete(job_id, "a" * 64, "PASS", "bundles", "dead-worker")
    assert jobs.get(job_id)["stage"] == "code"
    claimed = JobService(factory).claim("worker-new")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.status == "running"
    assert claimed.attempt == 2
    with factory() as session:
        events = session.scalars(
            select(EvaluationStageEvent).where(EvaluationStageEvent.job_id == job_id)
        ).all()
    assert any(event.status == "recovered" for event in events)
    jobs = JobService(factory)
    assert not jobs.heartbeat(job_id, "dead-worker")
    jobs.stage(job_id, "semantic", "completed", worker_id="dead-worker")
    jobs.fail(job_id, "evaluation_failed", "safe", "dead-worker")
    with pytest.raises(RuntimeError, match="no longer owned"):
        jobs.complete(job_id, "a" * 64, "PASS", "bundles", "dead-worker")
    assert jobs.get(job_id)["stage"] == "resolve_change"
    assert jobs.get(job_id)["status"] == "running"
    assert jobs.heartbeat(job_id, "worker-new")
    engine.dispose()


def _configured_project(app):
    with TestClient(app) as client:
        response = client.put(
            "/api/project-config",
            json={
                "jira_project": "SBX",
                "github_repository": "codeatlas-fyp/codeatlas-sandbox",
            },
        )
        assert response.status_code == 200
    with app.state.session_factory() as session:
        return session.scalar(select(ProjectConfig))


@pytest.mark.parametrize("changed_field", ["configuration", "encrypted_credential"])
def test_changed_connector_configuration_fails_before_live_collection(tmp_path, changed_field):
    from codeatlas.jobs.contract import SubmissionContract
    from codeatlas.jobs.sources import job_sources

    app = _app(tmp_path)
    project = _configured_project(app)
    job, _ = app.state.jobs.submit(project, 1, source_mode="live")
    contract = SubmissionContract.model_validate_json(job.submission_contract)
    with app.state.session_factory.begin() as session:
        connection = session.get(Connection, project.jira_connection_id)
        setattr(connection, changed_field, "changed")
    with pytest.raises(RuntimeError, match="changed since submission"):
        with job_sources(job, app.state.settings, app.state.session_factory, contract):
            pytest.fail("changed configuration must not be used")


def test_idempotency_key_rejects_different_pr_in_api(tmp_path):
    app = _app(tmp_path)
    _configured_project(app)
    with TestClient(app) as client:
        first = client.post(
            "/api/evaluations",
            json={"pull_request_number": 1},
            headers={"Idempotency-Key": "same-key"},
        )
        assert first.status_code == 202
        second = client.post(
            "/api/evaluations",
            json={"pull_request_number": 2},
            headers={"Idempotency-Key": "same-key"},
        )
        assert second.status_code == 409
        assert "idempotency_conflict" in second.text
        assert len(client.get("/api/evaluations").json()) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"pr_number": 2},
        {"base_sha": "new-base"},
        {"head_sha": "new-head"},
        {"actor_scope": "another-user"},
        {"source_mode": "fixtures"},
    ],
)
def test_idempotency_compares_full_request(tmp_path, changed):
    app = _app(tmp_path)
    project = _configured_project(app)
    jobs = app.state.jobs
    original = dict(pr_number=1, base_sha="base", head_sha="head")
    job, created = jobs.submit(project, idempotency_key="key", **original)
    assert created
    repeated, created = jobs.submit(project, idempotency_key="key", **original)
    assert repeated.id == job.id and not created
    with pytest.raises(SubmissionConflict):
        jobs.submit(project, idempotency_key="key", **(original | changed))


@pytest.mark.parametrize(
    "field,value",
    [
        ("github_repository", "other/repo"),
        ("jira_project", "OTHER"),
        ("github_connection_id", "other-connection"),
        ("id", "other-project"),
    ],
)
def test_idempotency_rejects_changed_project_scope(tmp_path, field, value):
    app = _app(tmp_path)
    project = _configured_project(app)
    app.state.jobs.submit(project, 1, "key")
    setattr(project, field, value)
    with pytest.raises(SubmissionConflict):
        app.state.jobs.submit(project, 1, "key")


def test_concurrent_submissions_and_terminal_retry_preserve_identity(tmp_path):
    app = _app(tmp_path)
    project = _configured_project(app)
    jobs = app.state.jobs
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: jobs.submit(project, 1, "retry"), range(8)))
    assert len({job.id for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    claimed = jobs.claim("worker")
    jobs.fail(claimed.id, "evaluation_failed", "safe", "worker")
    repeated, created = jobs.submit(project, 1, "retry")
    assert repeated.id == claimed.id and not created
    assert repeated.status == "failed"


def test_active_request_does_not_silently_discard_a_new_key(tmp_path):
    app = _app(tmp_path)
    project = _configured_project(app)
    app.state.jobs.submit(project, 1, "first")
    with pytest.raises(SubmissionConflict, match="already active"):
        app.state.jobs.submit(project, 1, "second")
    same, created = app.state.jobs.submit(project, 1)
    assert not created and same.idempotency_key == "first"


def test_request_fingerprint_migration_preserves_legacy_rows(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0001_application_state")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO evaluation_jobs
            (id, project_id, repository, pr_number, status, stage, attempt, created_at)
            VALUES ('legacy', 'project', 'owner/repo', 1, 'queued', 'queued', 0,
                    '2026-11-17 00:00:00')""")
        )
    command.upgrade(config, "head")
    assert "request_fingerprint" in {
        c["name"] for c in inspect(engine).get_columns("evaluation_jobs")
    }
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id, request_fingerprint FROM evaluation_jobs")).one()
        assert tuple(row) == ("legacy", None)
    engine.dispose()
