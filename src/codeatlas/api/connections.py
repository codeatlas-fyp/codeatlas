"""Credential validation and single-user project configuration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from codeatlas.collect.github.client import GitHubClient, GitHubClientConfig
from codeatlas.collect.jira.client import JiraClient
from codeatlas.db_models import Connection, ProjectConfig
from codeatlas.integrations.connections import (
    ConnectionService,
    ConnectionValidationError,
    validate_project,
    validate_repo,
)

router = APIRouter(prefix="/api")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JiraCredentials(Input):
    base_url: str
    email: str
    api_token: SecretStr


class GitHubCredentials(Input):
    personal_access_token: SecretStr


class ProjectSelection(Input):
    jira_project: str
    github_repository: str
    name: str = "Default project"

    @field_validator("jira_project")
    @classmethod
    def project_key(cls, value: str) -> str:
        return validate_project(value)

    @field_validator("github_repository")
    @classmethod
    def repository_name(cls, value: str) -> str:
        return validate_repo(value)


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_service(request: Request) -> ConnectionService:
    return ConnectionService(request.app.state.settings)


ServiceDep = Annotated[ConnectionService, Depends(get_service)]


@router.get("/setup")
def get_setup(session: SessionDep, service: ServiceDep) -> dict[str, object]:
    return service.setup(session)


@router.post("/connections/jira/validate")
def validate_jira(body: JiraCredentials, service: ServiceDep) -> dict[str, object]:
    try:
        metadata = service.validate_jira(
            body.base_url, body.email, body.api_token.get_secret_value()
        )
        return {"valid": True, "display_name": metadata["display_name"]}
    except ConnectionValidationError as error:
        raise HTTPException(422, detail={"code": error.code, "message": error.message}) from error


@router.post("/connections/jira", status_code=status.HTTP_201_CREATED)
def save_jira(body: JiraCredentials, session: SessionDep, service: ServiceDep) -> dict[str, object]:
    try:
        connection = service.save_jira(
            session, body.base_url, body.email, body.api_token.get_secret_value()
        )
        return {
            "provider": connection.provider,
            "connected": True,
            "display_name": connection.display_name,
        }
    except ConnectionValidationError as error:
        raise HTTPException(422, detail={"code": error.code, "message": error.message}) from error


@router.post("/connections/github/validate")
def validate_github(body: GitHubCredentials, service: ServiceDep) -> dict[str, object]:
    try:
        metadata = service.validate_github(body.personal_access_token.get_secret_value())
        return {"valid": True, "display_name": metadata["display_name"]}
    except ConnectionValidationError as error:
        raise HTTPException(422, detail={"code": error.code, "message": error.message}) from error


@router.post("/connections/github", status_code=status.HTTP_201_CREATED)
def save_github(
    body: GitHubCredentials, session: SessionDep, service: ServiceDep
) -> dict[str, object]:
    try:
        connection = service.save_github(session, body.personal_access_token.get_secret_value())
        return {
            "provider": connection.provider,
            "connected": True,
            "display_name": connection.display_name,
        }
    except ConnectionValidationError as error:
        raise HTTPException(422, detail={"code": error.code, "message": error.message}) from error


@router.put("/project-config")
def save_project_config(
    body: ProjectSelection,
    request: Request,
    session: SessionDep,
    service: ServiceDep,
) -> dict[str, object]:
    settings = request.app.state.settings
    jira = session.scalar(select(Connection).where(Connection.provider == "jira"))
    github = session.scalar(select(Connection).where(Connection.provider == "github"))
    if settings.source_mode == "fixtures":
        if (
            body.github_repository != "codeatlas-fyp/codeatlas-sandbox"
            or body.jira_project != "SBX"
        ):
            raise HTTPException(
                422,
                detail={
                    "code": "fixture_selection_invalid",
                    "message": (
                        "Fixture mode supports the recorded SBX project and sandbox repository."
                    ),
                },
            )
        if jira is None:
            jira = Connection(
                provider="jira",
                display_name="Fixture Jira",
                configuration="{}",
                encrypted_credential="",
                validation_status="fixture",
            )
            session.add(jira)
        if github is None:
            github = Connection(
                provider="github",
                display_name="Fixture GitHub",
                configuration="{}",
                encrypted_credential="",
                validation_status="fixture",
            )
            session.add(github)
        session.flush()
    else:
        if (
            jira is None
            or github is None
            or jira.validation_status != "valid"
            or github.validation_status != "valid"
        ):
            raise HTTPException(
                409,
                detail={
                    "code": "connections_required",
                    "message": "Connect and validate Jira and GitHub first.",
                },
            )
        try:
            jira_config, jira_token = service.credentials(session, "jira")
            with JiraClient(
                jira_config["base_url"], jira_config["email"], jira_token
            ) as jira_client:
                jira_keys = {str(item.get("key", "")) for item in jira_client.projects()}
            _, github_token = service.credentials(session, "github")
            github_client = GitHubClient(GitHubClientConfig(token=github_token))
            try:
                repositories = {
                    str(item.get("full_name", "")) for item in github_client.repositories()
                }
            finally:
                github_client.close()
            if body.jira_project not in jira_keys or body.github_repository not in repositories:
                raise HTTPException(
                    422,
                    detail={
                        "code": "selection_not_accessible",
                        "message": (
                            "Select a Jira project and GitHub repository "
                            "returned by the connected accounts."
                        ),
                    },
                )
        except ConnectionValidationError as error:
            raise HTTPException(
                409, detail={"code": error.code, "message": error.message}
            ) from error
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(
                502,
                detail={
                    "code": "catalog_unavailable",
                    "message": "Could not verify account access. Check the connections and retry.",
                },
            ) from error
    config = session.scalar(select(ProjectConfig).order_by(ProjectConfig.created_at).limit(1))
    if config is None:
        config = ProjectConfig(
            name=body.name,
            jira_project=body.jira_project,
            github_repository=body.github_repository,
            jira_connection_id=jira.id,
            github_connection_id=github.id,
        )
        session.add(config)
    else:
        config.name = body.name
        config.jira_project = body.jira_project
        config.github_repository = body.github_repository
        config.jira_connection_id = jira.id
        config.github_connection_id = github.id
    session.commit()
    session.refresh(config)
    return {
        "id": config.id,
        "name": config.name,
        "jira_project": config.jira_project,
        "github_repository": config.github_repository,
    }
