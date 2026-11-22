"""Server-managed policy and identity profiles are frozen into each job."""

import json

from fastapi.testclient import TestClient

from codeatlas.api.app import create_app
from codeatlas.config import DEFAULT_POLICY
from codeatlas.db import Base
from codeatlas.jobs.worker import run_worker
from tests.unit.test_application import _settings


def test_two_profiled_jobs_keep_their_inputs_after_configuration_changes(tmp_path) -> None:
    first_policy = DEFAULT_POLICY.model_dump(mode="json")
    second_policy = DEFAULT_POLICY.model_dump(mode="json")
    second_policy["semantic"]["top_k"] = 3
    profiles = {
        "profiles": {
            "first": {
                "policy": first_policy,
                "identity_map": {
                    "people": [
                        {
                            "jira_account_id": "account-a",
                            "github_login": "user-01",
                            "git_emails": [],
                        }
                    ]
                },
            },
            "second": {
                "policy": second_policy,
                "identity_map": {
                    "people": [
                        {
                            "jira_account_id": "account-b",
                            "github_login": "user-02",
                            "git_emails": [],
                        }
                    ]
                },
            },
        }
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(profiles), encoding="utf-8")
    settings = _settings(tmp_path).model_copy(update={"evaluation_profiles_path": path})
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with TestClient(app) as client:
        assert (
            client.put(
                "/api/project-config",
                json={
                    "jira_project": "SBX",
                    "github_repository": "codeatlas-fyp/codeatlas-sandbox",
                },
            ).status_code
            == 200
        )
        missing = client.post(
            "/api/evaluations", json={"pull_request_number": 1, "profile_id": "missing"}
        )
        assert missing.status_code == 422
        jobs = []
        for profile_id in ("first", "second"):
            response = client.post(
                "/api/evaluations", json={"pull_request_number": 1, "profile_id": profile_id}
            )
            assert response.status_code == 202
            jobs.append(response.json()["id"])
    assert jobs[0] != jobs[1]
    path.write_text("{}", encoding="utf-8")
    run_worker(settings, once=True)
    run_worker(settings, once=True)
    with TestClient(app) as client:
        for job_id, profile_id, expected in zip(jobs, ("first", "second"), (5, 3), strict=True):
            response = client.get(f"/api/evaluations/{job_id}/case")
            assert response.status_code == 200
            data = response.json()
            assert data["bundle"]["policy"]["semantic"]["top_k"] == expected
            assert data["bundle"]["policy_source"].startswith(
                f"server profile {profile_id}; sha256:"
            )
            assert data["bundle"]["identity_map_source"] == data["bundle"]["policy_source"]
            assert (
                data["bundle"]["identity_map"]["people"]
                == profiles["profiles"][profile_id]["identity_map"]["people"]
            )
            assert client.get(data["replay_url"]).json()["match"] is True
