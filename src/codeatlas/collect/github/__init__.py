"""GitHub REST collection (spec §6, step-9)."""

from codeatlas.collect.github.client import (
    GitHubClient,
    GitHubClientConfig,
    client_from_env,
)
from codeatlas.collect.github.recorder import (
    FixtureSource,
    Recorder,
    RecorderPaths,
    Sanitiser,
)

__all__ = [
    "FixtureSource",
    "GitHubClient",
    "GitHubClientConfig",
    "Recorder",
    "RecorderPaths",
    "Sanitiser",
    "client_from_env",
]
