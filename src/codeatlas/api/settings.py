"""Settings for the deployed web service (spec §13 "Web interface").

Kept apart from `codeatlas.config.Settings` so the service never needs Jira or GitHub tokens.
"""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """`CODEATLAS_*` environment variables read by the API and web pages."""

    model_config = SettingsConfigDict(env_prefix="CODEATLAS_", env_file=".env", extra="ignore")

    bundle_dir: Path = Path("bundles")
    # HTTP basic auth is on only when both are set (the deployed demo sets them).
    web_user: str | None = None
    web_password: SecretStr | None = None
    # Git SHA of the running build; the Dockerfile sets it from the GIT_SHA build arg.
    commit: str = "unknown"
    database_url: str = "postgresql+psycopg://codeatlas:codeatlas@localhost:5432/codeatlas"
    credential_key: SecretStr | None = None
    production: bool = False
    repo_cache_dir: Path = Path("repo-cache")
    repo_max_bytes: int = 2_000_000_000
    repo_cache_max_repositories: int = 10
    repo_fetch_timeout_seconds: int = 300
    source_mode: str = "live"
    jira_fixture_dir: Path = Path("tests/fixtures/jira")
    github_fixture_dir: Path = Path("tests/fixtures/github")
    worker_id: str = "worker-1"
    job_lease_seconds: int = 120

    def validate_runtime(self) -> None:
        if self.source_mode not in {"live", "fixtures"}:
            raise ValueError("CODEATLAS_SOURCE_MODE must be 'live' or 'fixtures'")
        if self.production:
            if not self.web_user or not self.web_password:
                raise ValueError(
                    "CODEATLAS_WEB_USER and CODEATLAS_WEB_PASSWORD are required in production"
                )
            if not self.credential_key:
                raise ValueError("CODEATLAS_CREDENTIAL_KEY is required in production")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("a PostgreSQL database is required in production")
