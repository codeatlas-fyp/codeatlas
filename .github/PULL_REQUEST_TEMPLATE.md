## Jira ticket

CA-<n>

## What changed

<!-- 1–3 bullets -->

## How tested

<!-- commands run, test names, manual steps -->

## Screenshots

<!-- UI changes only; delete otherwise -->

## Checklist

- [ ] PR title and every commit start with `CA-<n>: `
- [ ] Branch is `feature/CA-<n>-...` or `fix/CA-<n>-...`, targeting `develop`
- [ ] Timestamps are timezone-aware UTC (ISO-8601)
- [ ] `uv run ruff check . && uv run mypy src && uv run pytest` pass locally
