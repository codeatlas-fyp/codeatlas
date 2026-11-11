"""Construct fresh live or explicit fixture adapters for one durable evaluation job."""

import json
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select

from codeatlas.api.settings import WebSettings
from codeatlas.collect.github.client import GitHubClient, GitHubClientConfig
from codeatlas.collect.jira.client import JiraClient
from codeatlas.db_models import Connection, EvaluationJob, ProjectConfig
from codeatlas.errors import CollectionError
from codeatlas.integrations.connections import ConnectionService
from codeatlas.integrations.fixtures import fixture_sources
from codeatlas.integrations.repositories import RepositoryManager
from codeatlas.pipeline import ChangeSources


class PinnedGitHubSource:
    """Fail closed if the PR advances between catalog, fetch, and evidence collection."""

    def __init__(
        self, client: GitHubClient, repository: str, number: int, base_sha: str, head_sha: str
    ):
        self.client = client
        self.repository = repository
        self.number = number
        self.base_sha = base_sha
        self.head_sha = head_sha

    def pull_request(self, repository: str, number: int) -> dict:
        data = self.client.pull_request(repository, number)
        self._check(data)
        return data

    def commits(self, repository: str, number: int) -> list[dict]:
        self._assert_current()
        return self.client.commits(repository, number)

    def reviews(self, repository: str, number: int) -> list[dict]:
        self._assert_current()
        return self.client.reviews(repository, number)

    def _assert_current(self) -> None:
        self._check(self.client.pull_request(self.repository, self.number))

    def _check(self, pr: dict) -> None:
        base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        if base.get("sha") != self.base_sha or head.get("sha") != self.head_sha:
            raise CollectionError(
                "github",
                status=409,
                retryable=False,
                detail="pull request refs changed during evaluation",
            )


@contextmanager
def job_sources(
    job: EvaluationJob, settings: WebSettings, factory
) -> Iterator[tuple[object, ChangeSources]]:
    if settings.source_mode == "fixtures":
        jira, sources = fixture_sources(settings, job.pr_number)
        yield jira, sources
        return

    with factory() as session:
        project = session.scalar(select(ProjectConfig).where(ProjectConfig.id == job.project_id))
        jira_connection = session.scalar(select(Connection).where(Connection.provider == "jira"))
        github_connection = session.scalar(
            select(Connection).where(Connection.provider == "github")
        )
        if project is None or jira_connection is None or github_connection is None:
            raise RuntimeError("configured source connections are missing")
        jira_configuration = json.loads(jira_connection.configuration)
        jira_credential = (
            ConnectionService(settings).vault().decrypt(jira_connection.encrypted_credential)
        )
        github_credential = (
            ConnectionService(settings).vault().decrypt(github_connection.encrypted_credential)
        )
        repository = job.repository

    jira = JiraClient(jira_configuration["base_url"], jira_configuration["email"], jira_credential)
    github = GitHubClient(GitHubClientConfig(token=github_credential))
    try:
        pr = github.pull_request(repository, job.pr_number)
        base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        base_sha, head_sha = base.get("sha"), head.get("sha")
        if not isinstance(base_sha, str) or not isinstance(head_sha, str):
            raise CollectionError(
                "github", status=None, retryable=False, detail="PR refs unavailable"
            )
        if job.base_sha and (job.base_sha != base_sha or job.head_sha != head_sha):
            raise CollectionError(
                "github", status=409, retryable=False, detail="PR refs changed after enqueue"
            )
        base_ref = base.get("ref")
        if not isinstance(base_ref, str):
            raise CollectionError(
                "github", status=None, retryable=False, detail="PR base ref unavailable"
            )
        git = RepositoryManager(settings).prepare(
            repository, job.pr_number, base_ref, base_sha, head_sha, github_credential
        )
        if job.base_sha is None or job.head_sha is None:
            with factory() as session, session.begin():
                stored = session.get(EvaluationJob, job.id)
                if stored:
                    stored.base_sha, stored.head_sha = base_sha, head_sha
        pinned = PinnedGitHubSource(github, repository, job.pr_number, base_sha, head_sha)
        yield jira, ChangeSources(github=pinned, git=git)
    finally:
        github.close()
        jira.close()
