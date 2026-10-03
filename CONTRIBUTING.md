# Contributing to CodeAtlas

All three of us agreed on these rules at the Day 1 kickoff. Follow them from the first commit.
CodeAtlas will later check its own PRs against these rules, so following them exactly matters.

## 1. Names

| Thing | Name |
|---|---|
| GitHub org | `codeatlas-fyp` |
| Product repo | `codeatlas-fyp/codeatlas` (this repo) |
| Demo target repo | `codeatlas-fyp/codeatlas-sandbox` |
| Jira project for our own team tasks | `CA` |
| Jira project for sandbox requirements used in demos | `SBX` |

## 2. Branches

| Branch | Purpose | Merges into |
|---|---|---|
| `main` | Protected. Deploys. | — (release PRs from `develop` only) |
| `develop` | Integration branch. Default branch for PRs. | `main`, for releases |
| `feature/CA-<n>-<short-name>` | New work for one Jira ticket | `develop` |
| `fix/CA-<n>-<short-name>` | Bug fix for one Jira ticket | `develop` |

- `<short-name>` is lowercase kebab-case, e.g. `feature/CA-4-jira-changelog-fetcher`.
- Each branch covers one ticket.
- Branch from the latest `develop`:
  `git switch develop && git pull && git switch -c feature/CA-4-jira-changelog-fetcher`

## 3. Commits

Format: `CA-<n>: <imperative message>`

```
CA-4: add Jira changelog fetcher
CA-7: fix timezone handling in commit parser
```

- Start every commit with a Jira key followed by a colon. CI rejects commits that don't
  (see `scripts/check_commit_msgs.py`).
- Write the message in the imperative ("add", "fix", "remove"), not "added" or "fixes".
- Keep the first line at 72 characters or fewer. Put details in the body after a blank line.
- `SBX-<n>` keys belong in `codeatlas-sandbox` only, never in this repo.

## 4. Pull requests

- **feature/fix → `develop`** needs **1 approval + green CI + a code-owner review**
  (see `CODEOWNERS`).
- **`develop` → `main`** is for releases only. Title it `Release vX.Y.Z`.
- Start the PR title with the key: `CA-4: add Jira changelog fetcher`.
- Fill in the PR template. You can't approve your own PR.
- Keep the `CA-<n>: ` prefix in the final commit title when you squash-merge.

## 5. Conventions

- **Python 3.12**, managed with **uv**. The package lives under `src/codeatlas/`.
- **Timestamps are UTC ISO-8601** everywhere: storage, APIs, logs, fixtures.
  Example: `2026-10-03T09:00:00Z`. Only use timezone-aware datetimes:
  `datetime.now(timezone.utc).isoformat()`. Never use naive `datetime.now()` or
  `datetime.utcnow()`. The ruff `DTZ` rules flag them.
- **Secrets** (Jira tokens, GitHub tokens) go in the shared password manager and GitHub Actions
  secrets. Never put them in the repo, fixtures or commit messages.
- **Fixtures**: scrub emails and tokens from saved API responses before committing them.

## 6. Layout and owners

```
src/codeatlas/
  schema/          Pydantic evidence models (frozen Day 2)   all three
  collect/github/  GitHub collectors                         Yusra
  collect/jira/    Jira collectors                           Areej
  analyze/         tree-sitter changed entities              Yusra
  evaluate/rules/  rule implementations                      Areej
  evaluate/verdict.py
  bundle/          canonical JSON, hashing, replay           Saleha
  api/             FastAPI app                               Saleha
  cli.py
web/               POC UI                                    Saleha
tests/unit/  tests/fixtures/jira/  tests/fixtures/github/
docs/spikes/  docs/decisions/
```

## 7. Local setup

```bash
# Install uv: https://docs.astral.sh/uv/  (or: pip install uv)
uv sync                       # creates .venv with Python 3.12 and all deps
uv run pre-commit install     # run ruff and file fixers on every commit
```

Before pushing, run the same checks as CI:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest --cov=codeatlas
```

Run the API: `uv run uvicorn codeatlas.api.app:app --reload`, then open http://localhost:8000/health.
Or with Docker: `docker build -t codeatlas . && docker run -p 8000:8000 codeatlas`.

## 8. Jira tickets

Create `CA-1` to `CA-12` for the Day 1–2 tasks before anyone starts coding, so every branch uses a
real key from the first hour. `CA-1` is the repository setup (`feature/CA-1-repo-setup`).

## 9. Branch protection (repo admin, one time)

Protection works on the free plan because the repo is **public**. If it ever goes private, claim
GitHub Education benefits for the org first.

Set this up under *Settings → Branches* (or *Rules → Rulesets*) for both `main` and `develop`:

- Require a pull request before merging, with 1 approval
- Require review from Code Owners
- Require the status check `ci` to pass
- Block force pushes and deletion

Also make **`develop`** the default branch, so new PRs target it automatically.
