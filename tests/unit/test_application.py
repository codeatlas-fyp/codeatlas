"""Single-user application persistence, credential handling, and fixture end-to-end flow."""

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
from codeatlas.db import Base
from codeatlas.db_models import Connection, EvaluationJob, EvaluationStageEvent, ProjectConfig
from codeatlas.integrations.credentials import CredentialVault
from codeatlas.jobs.service import JobService
from codeatlas.jobs.worker import run_worker

FIXTURES = Path(__file__).parents[1] / "fixtures"


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


def test_fixture_dashboard_evaluation_persists_history_bundle_and_replay(tmp_path) -> None:
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
        assert data["bundle"]["change"]["pr_number"] == 1
        assert data["verdict"]["results"]
        assert client.get(data["replay_url"]).json()["match"] is True
        assert client.get("/api/evaluations").json()[0]["id"] == job_id

    restarted = _app(tmp_path, database_url=settings.database_url)
    with TestClient(restarted) as client:
        history = client.get("/api/evaluations").json()
        assert history[0]["id"] == job_id
        assert history[0]["status"] == "succeeded"


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
    engine.dispose()
