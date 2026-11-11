"""Validate and persist Jira/GitHub connections without returning credential material."""

import json
import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from codeatlas.api.settings import WebSettings
from codeatlas.collect.github.client import GitHubClient, GitHubClientConfig
from codeatlas.collect.jira.client import JiraClient
from codeatlas.db_models import Connection, ProjectConfig
from codeatlas.integrations.credentials import CredentialVault

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_PROJECT = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class ConnectionValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_jira_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ConnectionValidationError(
            "invalid_jira_url", "Use the HTTPS URL of a Jira Cloud site."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConnectionValidationError(
            "invalid_jira_url", "Enter the Jira site URL without a path."
        )
    return f"https://{parsed.netloc}"


def validate_repo(value: str) -> str:
    if not _REPO.fullmatch(value) or any(part in {".", ".."} for part in value.split("/")):
        raise ConnectionValidationError(
            "invalid_repository", "Choose a repository from the GitHub account."
        )
    return value


def validate_project(value: str) -> str:
    if not _PROJECT.fullmatch(value):
        raise ConnectionValidationError("invalid_project", "Choose a valid Jira project key.")
    return value


class ConnectionService:
    def __init__(self, settings: WebSettings):
        self.settings = settings

    def vault(self) -> CredentialVault:
        key = self.settings.credential_key
        if key is None:
            raise ConnectionValidationError(
                "credential_encryption_unavailable",
                "Configure CODEATLAS_CREDENTIAL_KEY before saving connections.",
            )
        return CredentialVault(key.get_secret_value())

    def validate_jira(self, base_url: str, email: str, token: str) -> dict[str, str]:
        safe_url = validate_jira_url(base_url)
        try:
            with JiraClient(safe_url, email, token) as client:
                user = client.current_user()
        except Exception as error:
            raise _provider_error("jira", error) from error
        return {
            "display_name": str(
                user.get("displayName") or user.get("emailAddress") or "Jira account"
            )
        }

    def validate_github(self, token: str) -> dict[str, str]:
        client = GitHubClient(GitHubClientConfig(token=token))
        try:
            user = client.authenticated_user()
        except Exception as error:
            raise _provider_error("github", error) from error
        finally:
            client.close()
        return {"display_name": str(user.get("name") or user.get("login") or "GitHub account")}

    def save_jira(self, session: Session, base_url: str, email: str, token: str) -> Connection:
        metadata = self.validate_jira(base_url, email, token)
        return self._save(
            session,
            provider="jira",
            name=metadata["display_name"],
            configuration={"base_url": validate_jira_url(base_url), "email": email},
            credential=token,
        )

    def save_github(self, session: Session, token: str) -> Connection:
        metadata = self.validate_github(token)
        return self._save(
            session,
            provider="github",
            name=metadata["display_name"],
            configuration={},
            credential=token,
        )

    def _save(
        self,
        session: Session,
        *,
        provider: str,
        name: str,
        configuration: dict[str, str],
        credential: str,
    ) -> Connection:
        connection = session.scalar(select(Connection).where(Connection.provider == provider))
        if connection is None:
            connection = Connection(
                provider=provider, display_name=name, configuration="{}", encrypted_credential=""
            )
            session.add(connection)
        connection.display_name = name
        connection.configuration = json.dumps(configuration, sort_keys=True)
        connection.encrypted_credential = self.vault().encrypt(credential)
        connection.validation_status = "valid"
        session.commit()
        session.refresh(connection)
        return connection

    def credentials(self, session: Session, provider: str) -> tuple[dict[str, str], str]:
        connection = session.scalar(select(Connection).where(Connection.provider == provider))
        if connection is None or connection.validation_status != "valid":
            raise ConnectionValidationError(
                "connection_required", f"Connect a valid {provider.title()} account first."
            )
        config = json.loads(connection.configuration)
        return config, self.vault().decrypt(connection.encrypted_credential)

    def setup(self, session: Session) -> dict[str, object]:
        connections = session.scalars(select(Connection)).all()
        by_provider = {item.provider: item for item in connections}
        project = session.scalar(select(ProjectConfig).order_by(ProjectConfig.created_at).limit(1))
        return {
            "jira": _connection_public(by_provider.get("jira")),
            "github": _connection_public(by_provider.get("github")),
            "project": None
            if project is None
            else {
                "id": project.id,
                "name": project.name,
                "jira_project": project.jira_project,
                "github_repository": project.github_repository,
                "updated_at": project.updated_at.isoformat(),
            },
            "source_mode": self.settings.source_mode,
        }


def _connection_public(connection: Connection | None) -> dict[str, object]:
    if connection is None:
        return {"connected": False}
    return {
        "connected": connection.validation_status in {"valid", "fixture"},
        "mode": connection.validation_status,
        "display_name": connection.display_name,
        "updated_at": connection.updated_at.isoformat(),
        "configuration": json.loads(connection.configuration),
    }


def _provider_error(provider: str, error: Exception) -> ConnectionValidationError:
    status = getattr(error, "status", None)
    if status in {401, 403}:
        return ConnectionValidationError(
            f"{provider}_credentials_rejected",
            f"{provider.title()} rejected the credentials or they lack read permission.",
        )
    return ConnectionValidationError(
        f"{provider}_unavailable",
        f"Could not validate the {provider.title()} connection. Check the site and try again.",
    )
