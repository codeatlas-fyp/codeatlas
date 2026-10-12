# CodeAtlas

CodeAtlas evaluates a proposed change against the Jira requirement it implements. It collects
requirement and lifecycle evidence, applies policy rules, and stores a hashed evidence bundle with
an explainable verdict.

- How we work (branches, commits, PRs, conventions): [CONTRIBUTING.md](CONTRIBUTING.md)
- Decisions: [docs/decisions/](docs/decisions/)
- Demo target: [codeatlas-sandbox](https://github.com/codeatlas-fyp/codeatlas-sandbox) (Jira project `SBX`)

## Current scope

The Phase 1 CLI can collect Jira evidence, evaluate a stored evidence bundle offline, replay a
stored verdict to check reproducibility, and show the verdict and its rule results. A change file
stands in for GitHub change evidence in this phase; GitHub collection is not part of the Phase 1
workflow.

Run `uv run codeatlas --help` for available commands and options.

## Development setup

```bash
uv sync
uv run pytest
```

To run the API locally:

```bash
uv run uvicorn codeatlas.api.app:app --reload   # http://localhost:8000/health
```
