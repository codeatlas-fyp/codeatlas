# Graph view (graph-view-v1)

Status on branch `feature/graph-view` (from `feature/application-integration`, `cf38b7d`),
20 Nov 2026. Owner: Saleha Muhammad (builder). Route and UI: Areej Hamid.

## What it is

`src/codeatlas/graph/view.py` turns one stored evidence bundle into the `GraphView` payload that
Areej's UI draws. It uses her `graph-view-v1` schema in `src/codeatlas/schema/integration.py`
as it is: no schema change.

```python
from codeatlas.evaluate.links import bundle_links
from codeatlas.graph import build_graph_view

view = build_graph_view(bundle, bundle_links(bundle))  # GraphView
```

The function is pure: no I/O, no clock. The same bundle always gives the same JSON.

## Nodes

| `kind` | `id` | From | Other fields |
|---|---|---|---|
| `change` | `change:<repo>#<pr>@<head_sha>` | `bundle.change` | `revision` = head |
| `requirement` | `req:<KEY>` | work items, requirement versions, criteria, links | none |
| `criterion` | `crit:<criterion_id>` (e.g. `crit:SBX-2:v2:ac1`) | `bundle.criteria` | none |
| `module`, `class`, `function`, `method` | `code:<entity_id>@<revision>` | `bundle.entities` | `entity_id`, `file`, `start_line`, `end_line`, `revision` |

`revision` is the commit the file was parsed at: the merge base for a file the PR deletes, head
for every other file (the same rule as `pipeline._code`).

## Edges

| `kind` | `src` → `dst` | `confident` | `resolution_status` | `evidence_refs` |
|---|---|---|---|---|
| `CHANGES` | change → code | true | `added`, `modified`, `deleted` (sorted, joined with `,` if mixed) | changed-entity evidence ids |
| `HAS_CRITERION` | requirement → criterion | true | `acceptance_criteria` or `derived_from_description` | none |
| `TRACE` | criterion (or requirement) → code | true only for `observed` and `deterministically_derived` | the link state in lower case: `observed`, `deterministically_derived`, `semantic_candidate`, `conflicting`, `unresolved` | the link's evidence ids |
| `IMPORTS` | code → code | as extracted | `resolved` / `uncertain` | none |
| `CALLS` | code → code | as extracted | `resolved` / `uncertain` | none |

`id` is `edge:` plus the SHA-256 of kind, ends and status, so an edge keeps its id across runs.
Nodes are sorted by kind (change, requirement, criterion, code) then id; edges by kind, ends, id.

## Rules

- **Nothing is inferred.** TRACE edges come only from `bundle_links(bundle)`, the same links the
  rules use. CALLS and IMPORTS edges never create or change a TRACE edge (tested).
- **Nothing is invented.** An edge whose end is not in the bundle is dropped and counted in
  `limitations`. Imports of modules outside the repository (`dataclasses`, `datetime`) are left
  out and counted too, but they are expected, so they do not make the graph incomplete.
- **Guesses stay guesses.** `confident` and `resolution_status` are copied from the bundle and
  the links; the UI should draw `confident: false` differently.

## Status and limitations

| `status` | When |
|---|---|
| `unavailable` | the bundle has no code entities |
| `incomplete` | the bundle has no CALLS extraction (collected before PR #53), or a changed hunk maps to no entity, or an edge was dropped |
| `available` | none of the above |

Every reason is a line in `limitations`. These two lines are always present:
`Static edges do not prove execution.` and
`CALLS and IMPORTS edges are never used to create trace links.`

All five demo bundles in `deploy/bundles/` are `incomplete`, because they predate CALLS
extraction.

## Fixtures for the UI

| File | Built from | Shows |
|---|---|---|
| `tests/fixtures/graph_view/demo_legacy.json` | demo bundle `1ec84cb5…` (FAIL case) | a real `incomplete` graph: 36 nodes, 9 edges, no CALLS |
| `tests/fixtures/graph_view/sample_with_calls.json` | `tests/samples.py` bundle plus synthetic entities and edges | an `available` graph with every edge kind, confident and unconfident CALLS, and a semantic-candidate TRACE edge |

The second fixture is synthetic: it is for building the UI, not evaluation evidence.
`tests/unit/test_graph_view.py` rebuilds both and fails if they differ. After an intended change,
regenerate them with:

```powershell
$env:UPDATE_GRAPH_FIXTURES = "1"; uv run pytest tests/unit/test_graph_view.py -q; Remove-Item Env:UPDATE_GRAPH_FIXTURES
```

## Using it in the route (Areej)

`get_graph` in `src/codeatlas/api/routes.py` can call the builder instead of building the view
inline:

```python
from codeatlas.graph import build_graph_view


@router.get("/cases/{bundle_hash}/graph", response_model=GraphView)
def get_graph(bundle_hash: str, store: Store) -> GraphView:
    case, links = store.links(bundle_hash)
    return build_graph_view(case.bundle, links)
```

Then remove the imports this leaves unused (`sha256_hex`, `GraphEdge`, `GraphNode`,
`GraphSnapshot`). With this change, `test_web.py`, `test_application.py` and
`test_integration_contracts.py` still pass (70 tests, checked 20 Nov).

## Open questions for the contract

- The schema has no field for a node label, a "changed" flag, or extractor versions. Today the
  label is the id, "changed" is a `CHANGES` edge, and extractor versions are named in
  `limitations`. If the UI needs them as fields, that is a `graph-view-v2` change.
- CALLS edges have no source span, because `CodeEdge` has none. Adding one changes the bundle
  schema and needs a migration plan.

## Checks run

| Check | Result |
|---|---|
| `uv run pytest tests/unit/test_graph_view.py -q` | 15 passed |
| `uv run pytest -q` | 685 passed |
| `uv run ruff format --check src tests`, `uv run ruff check src tests` | clean |
| `uv run mypy src` | no errors in `src/codeatlas/graph/`; 43 errors already on `feature/application-integration` in other files |
| `uv run lint-imports` | `codeatlas.graph` added to K2 and K3 in `.importlinter`; it breaks neither. K2 and K3 are already broken on `feature/application-integration` (`api/catalog.py`, `api/connections.py`, `api/evaluations.py` import `collect`) |
| `pre-commit` (all hooks except gitleaks, which could not install offline) | passed |
