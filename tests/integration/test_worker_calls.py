"""CALLS extraction is executed by the persisted application worker."""

import pytest
from fastapi.testclient import TestClient

from codeatlas import pipeline
from codeatlas.errors import CollectionError
from codeatlas.integrations import fixtures
from codeatlas.jobs.worker import run_worker
from tests.unit.test_application import _app, _settings
from tests.unit.test_c5 import NOW, _bundle, _governance


def test_missing_calls_parser_fails_closed(monkeypatch) -> None:
    class EmptyGit:
        def list_files(self, sha):
            return []

    def unavailable():
        raise ImportError("missing parser")

    monkeypatch.setattr(pipeline.code_analysis, "_parser", unavailable)
    bundle = _bundle(governance=_governance(None), reviews=[], commits=[], changed=[])
    with pytest.raises(CollectionError, match="CALLS parser is unavailable"):
        pipeline._code(bundle.change, [], [], EmptyGit(), NOW)


def test_fixture_worker_freezes_calls_edges(tmp_path, monkeypatch) -> None:
    original = fixtures._commit

    def with_calls(repo, files, parents, message):
        files = dict(files)
        files["loans.py"] += (
            "\n\ndef local_helper():\n    return 1\n\ndef invoke():\n    return local_helper()\n"
        )
        return original(repo, files, parents, message)

    monkeypatch.setattr(fixtures, "_commit", with_calls)
    settings = _settings(tmp_path)
    app = _app(tmp_path, database_url=settings.database_url)
    with TestClient(app) as client:
        response = client.put(
            "/api/project-config",
            json={"jira_project": "SBX", "github_repository": "codeatlas-fyp/codeatlas-sandbox"},
        )
        assert response.status_code == 200
        response = client.post("/api/evaluations", json={"pull_request_number": 1})
        assert response.status_code == 202
        job_id = response.json()["id"]
    run_worker(settings, once=True)
    with TestClient(app) as client:
        response = client.get(f"/api/evaluations/{job_id}/case")
        assert response.status_code == 200
        bundle = response.json()["bundle"]
        assert bundle["extractor_versions"]["calls"]
        assert any(
            e["kind"] == "CALLS" and e["src"] == "loans.invoke" and e["dst"] == "loans.local_helper"
            for e in bundle["edges"]
        )
        graph = client.get(f"/api/cases/{response.json()['bundle_hash']}/graph")
        assert graph.status_code == 200
        assert any(
            edge["kind"] == "CALLS"
            and "loans.invoke@" in edge["src"]
            and "loans.local_helper@" in edge["dst"]
            for edge in graph.json()["edges"]
        )
