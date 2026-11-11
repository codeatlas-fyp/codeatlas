"""Provider catalogs used by project selection; fixture responses are explicit mode only."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from codeatlas.api.connections import ServiceDep, SessionDep
from codeatlas.collect.github.client import GitHubClient, GitHubClientConfig
from codeatlas.collect.jira.client import JiraClient
from codeatlas.db_models import ProjectConfig
from codeatlas.integrations.connections import ConnectionService, ConnectionValidationError
from codeatlas.integrations.fixtures import FIXTURE_REPO, fixture_pull_requests

router = APIRouter(prefix="/api")


@router.get("/jira/projects")
def jira_projects(
    request: Request, session: SessionDep, service: ServiceDep
) -> list[dict[str, str]]:
    settings = request.app.state.settings
    if settings.source_mode == "fixtures":
        keys = sorted(
            {
                path.name.split(".", 1)[0].split("-", 1)[0]
                for path in settings.jira_fixture_dir.glob("*.issue.json")
            }
        )
        return [{"key": key, "name": f"Fixture project {key}"} for key in keys]
    try:
        config, token = service.credentials(session, "jira")
        with JiraClient(config["base_url"], config["email"], token) as client:
            return [
                {
                    "key": str(item.get("key", "")),
                    "name": str(item.get("name", item.get("key", ""))),
                }
                for item in client.projects()
                if item.get("key")
            ]
    except Exception as error:
        raise _catalog_error("Jira", error) from error


@router.get("/github/repositories")
def github_repositories(
    request: Request, session: SessionDep, service: ServiceDep
) -> list[dict[str, str]]:
    if request.app.state.settings.source_mode == "fixtures":
        return [
            {"full_name": FIXTURE_REPO, "name": FIXTURE_REPO.split("/")[-1], "private": "false"}
        ]
    client = _github_client(session, service)
    try:
        return [
            {
                "full_name": str(item.get("full_name", "")),
                "name": str(item.get("name", "")),
                "private": str(item.get("private", False)).lower(),
            }
            for item in client.repositories()
            if item.get("full_name")
        ]
    except Exception as error:
        raise _catalog_error("GitHub", error) from error
    finally:
        client.close()


@router.get("/github/repositories/{owner}/{repo}/pull-requests")
def pull_requests(
    owner: str,
    repo: str,
    request: Request,
    session: SessionDep,
    service: ServiceDep,
) -> list[dict[str, object]]:
    repository = f"{owner}/{repo}"
    config = session.scalar(select(ProjectConfig).order_by(ProjectConfig.created_at).limit(1))
    if config is None or config.github_repository != repository:
        raise HTTPException(
            403,
            detail={
                "code": "repository_not_selected",
                "message": "Select a repository from the connected GitHub account first.",
            },
        )
    if request.app.state.settings.source_mode == "fixtures":
        raw_items = fixture_pull_requests(request.app.state.settings)
    else:
        client = _github_client(session, service)
        try:
            raw_items = client.pull_requests(repository)
        except Exception as error:
            raise _catalog_error("GitHub", error) from error
        finally:
            client.close()
    return [_pr_public(item) for item in raw_items]


def _github_client(session: Session, service: ConnectionService) -> GitHubClient:
    try:
        _, token = service.credentials(session, "github")
        return GitHubClient(GitHubClientConfig(token=token))
    except ConnectionValidationError as error:
        raise HTTPException(409, detail={"code": error.code, "message": error.message}) from error


def _pr_public(item: dict) -> dict[str, object]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    head = item.get("head") if isinstance(item.get("head"), dict) else {}
    return {
        "number": int(item.get("number", 0)),
        "title": str(item.get("title", "")),
        "state": str(item.get("state", "")),
        "author": str(user.get("login", "")),
        "base_ref": str(base.get("ref", "")),
        "base_sha": str(base.get("sha", "")),
        "head_ref": str(head.get("ref", "")),
        "head_sha": str(head.get("sha", "")),
        "html_url": str(item.get("html_url", "")),
    }


def _catalog_error(provider: str, error: Exception) -> HTTPException:
    # Intentionally discard provider payloads/URLs so tokens or private metadata cannot leak.
    code = getattr(error, "status", None)
    if code in {401, 403}:
        detail = f"{provider} rejected the saved credentials or they lack read permission."
    else:
        detail = f"Could not load {provider} data. Check the connection and retry."
    return HTTPException(502, detail={"code": f"{provider.lower()}_unavailable", "message": detail})
