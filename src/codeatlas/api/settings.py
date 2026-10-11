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
