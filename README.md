# CodeAtlas

Traceability between Jira requirements and GitHub code changes. This is our final year project.

- How we work (branches, commits, PRs, conventions): [CONTRIBUTING.md](CONTRIBUTING.md)
- Decisions: [docs/decisions/](docs/decisions/)
- Demo target: [codeatlas-sandbox](https://github.com/codeatlas-fyp/codeatlas-sandbox) (Jira project `SBX`)

## Quick start

```bash
uv sync
uv run pytest
uv run uvicorn codeatlas.api.app:app --reload   # http://localhost:8000/health
```
