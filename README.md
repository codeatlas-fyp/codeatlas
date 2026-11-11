# CodeAtlas

CodeAtlas connects Jira Cloud requirements and GitHub pull requests, runs the existing evidence
collection and evaluation pipeline in a durable worker, and stores immutable, hashed evidence
bundles that can be inspected and replayed.

The web application is served by FastAPI with server-rendered pages. PostgreSQL stores connection
metadata, selected project configuration, job progress, and bundle references. Bundle JSON remains
in the existing content-addressed filesystem store.

## Prerequisites

- Docker Engine and Docker Compose (recommended for the complete app).
- For local Python development: Python 3.12 and `uv`.
- For live evaluation: Jira Cloud API token and a GitHub personal access token with read access to
  the selected repository, pull requests, commits, and reviews.

## Docker setup

Copy `.env.example` to `.env`. Set unique values for the Postgres password and web Basic Auth
credentials. Keep those values URL-safe (letters, digits, and hyphens) because Compose assembles the
database URL from them.

Create a Fernet encryption key once, store it in a password manager, and set it as
`CODEATLAS_CREDENTIAL_KEY` in `.env`:

```powershell
uv run --with cryptography python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Start PostgreSQL, apply Alembic migrations, then start the API and worker:

```powershell
docker compose up --build
```

Open <http://localhost:8000>. The API and worker are separate processes sharing Postgres, the
bundle volume, and the managed repository cache. Compose runs the migration service before either
process starts. The UI is part of the FastAPI service; there is no separate frontend service.

## Local development

Install dependencies and start PostgreSQL (for example, with `docker compose up db -d`). Then run
migrations and start the API and worker in separate terminals:

```powershell
uv sync
$env:CODEATLAS_DATABASE_URL = "postgresql+psycopg://codeatlas:YOUR_PASSWORD@localhost:5432/codeatlas"
$env:CODEATLAS_CREDENTIAL_KEY = "YOUR_FERNET_KEY"
$env:CODEATLAS_WEB_USER = "areej"
$env:CODEATLAS_WEB_PASSWORD = "YOUR_WEB_PASSWORD"
uv run alembic upgrade head
uv run uvicorn codeatlas.api.app:app --reload
```

Worker terminal:

```powershell
$env:CODEATLAS_DATABASE_URL = "postgresql+psycopg://codeatlas:YOUR_PASSWORD@localhost:5432/codeatlas"
$env:CODEATLAS_CREDENTIAL_KEY = "YOUR_FERNET_KEY"
$env:CODEATLAS_WEB_USER = "areej"
$env:CODEATLAS_WEB_PASSWORD = "YOUR_WEB_PASSWORD"
uv run python -m codeatlas.jobs.worker
```

`CODEATLAS_PRODUCTION=true` is set by Compose and makes Basic Auth, a Fernet key, and PostgreSQL
mandatory at application startup. Do not use sample credentials in a shared or public deployment.

## Fixture mode

Fixture mode is explicit and never used as a fallback after a live provider error. It uses the
sanitized Jira recordings and the recorded sandbox PRs (1 and 2), and needs no provider tokens:

```powershell
$env:CODEATLAS_SOURCE_MODE = "fixtures"
```

The fixture dashboard identifies itself as fixture mode. The same worker and pipeline execute,
including creation of an actual bundle, but external source calls use committed recordings. Set
`CODEATLAS_SOURCE_MODE=live` for real Jira and GitHub accounts.

## Connect accounts and run an evaluation

1. Open `/setup` and provide the Jira Cloud site URL, account email, and API token.
2. Validate and save the GitHub personal access token.
3. Select an accessible Jira project and GitHub repository, then save the selection.
4. Select an open pull request and click **Evaluate**.
5. Watch persisted worker stage updates. When complete, inspect the verdict, evidence, requirement
   timeline, trace links, and replay view.

Credentials are validated before saving and encrypted with Fernet authenticated encryption. The
database stores the encrypted token and non-secret connection metadata. API responses never return
the stored token. Use HTTPS when exposing the app beyond localhost. Jira and GitHub operations are
read-only.

### Encryption key provisioning and rotation

Generate the key once with the command above. Back it up separately from the database. Losing the
key makes saved credentials unreadable. To rotate it, decrypt and re-encrypt every saved credential
with a controlled maintenance procedure before replacing the old key; do not simply change the
environment value.

## Migrations and tests

Run migrations after changing the schema:

```powershell
uv run alembic upgrade head
```

Run the unit and integration test suite:

```powershell
uv sync
uv run pytest
```

Automated application tests use SQLite where appropriate and fixture/provider mocks; they do not
prove access to a real Jira site or GitHub account. Live verification requires running the Compose
stack, entering valid read-only credentials in `/setup`, selecting a project/repository/PR, and
confirming the resulting bundle can replay. Do not report live provider integration as tested until
that manual run has been completed.

## Known limitations

- This milestone is single-user and uses shared Basic Auth; it is not a multi-user service.
- Jira Cloud and GitHub PATs are supported. OAuth, GitHub Apps, webhooks, and scheduled sync are
  outside this milestone.
- Jobs are durable and lease-recovered, but complex cancellation/resume behavior is not included.
- Repository fetches use a bounded local cache and verify exact PR base/head SHAs. A changed or
  unavailable ref fails the job rather than evaluating cached stale content.
- Jira comments are not currently collected as evaluation evidence; issue fields, changelogs,
  statuses, and configured group membership are collected by the existing pipeline.
- Bundle identity and evidence schema semantics are unchanged. A new collection can have a new hash
  even when source evidence appears unchanged because bundles record run-specific data.

## Existing CLI

The original CLI remains available for direct collection, offline evaluation, replay, and verdict
display:

```powershell
uv run codeatlas --help
```
