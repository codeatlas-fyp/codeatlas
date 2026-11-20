# CALLS evaluation (SG-07)

Status on branch `feature/calls-eval` (from `fix/calls-local-scope`), 20 Nov 2026. Owner:
Saleha Muhammad. This is the tooling only: **no accuracy figure exists yet**, because no call
sites have been labelled.

## Why

The 58 call-graph tests show that the resolver follows the rules we wrote. They don't show how
often it is right on real code. This measures that on call sites that people label without
seeing CodeAtlas's answer, and reports confident errors apart from guesses and omissions.

## Files

| File | What it does |
|---|---|
| `src/codeatlas/analyze/calls.py` | new `resolve_call_sites()`: the resolver's answer per call site (file, line, column, caller, callee, confident). `build_call_graph` now merges these into edges; its output is unchanged (same 4,181 edges on Airflow 1.10.7, checked 20 Nov) |
| `src/codeatlas/analyze/calls_eval.py` | pure functions: find every call site, seeded sample, blind sheet, label parsing, agreement, scoring, report |
| `scripts/calls_eval.py` | command line: `sample`, `agree`, `score` (reads and writes the files) |
| `tests/unit/test_calls_eval.py` | 9 tests on a synthetic repository; they check the arithmetic, not accuracy |

## Procedure

1. **Freeze the source.** Check out the repository at a fixed commit and note the full SHA.
2. **Draw the sample.** Every call expression counts, including the ones the resolver never
   records (`handlers[0]()`, `super().run()`), so skipped calls show up as omissions.

   ```powershell
   uv run python scripts/calls_eval.py sample --root C:\path\to\airflow --include airflow `
       --repo apache/airflow --revision <full sha> --n 40 --seed 2026 --out sheet.csv
   ```

   The sheet holds `site_id, repo, revision, file, line, column, call_text, line_text, gold,
   notes`. It never shows CodeAtlas's answer.
3. **Label blind, twice.** Two team members each fill `gold` in their own copy, reading the
   source at that commit, without running CodeAtlas on it or looking at its graph.
4. **Compare.** `uv run python scripts/calls_eval.py agree --first a.csv --second b.csv` prints
   the raw agreement and every disagreement.
5. **Settle.** A third person decides each disagreement and writes the settled sheet. Keep both
   original sheets unchanged.
6. **Score.**

   ```powershell
   uv run python scripts/calls_eval.py score --root C:\path\to\airflow --include airflow `
       --labels settled.csv --out report.md
   ```

## Labelling rules for `gold`

| Write | When | Example |
|---|---|---|
| the entity id | the call runs a function, method or class defined in the repository | `airflow.models.dag.DAG.run`; a class call is the class's `__init__` if the class defines one (`pkg.mod.Class.__init__`), else the class (`pkg.mod.Class`) |
| `EXTERNAL` | it runs code outside the repository: standard library, third-party, builtin | `json.dumps(...)`, `print(...)`, `session.query(...)` |
| `UNKNOWN` | the source text alone does not decide it, even for a person | a callback passed in as a parameter; `getattr(obj, name)()` |
| `SKIP` | not a real call site (rare) | text that only looks like a call |

Entity ids are dotted module paths from the repository root, as CodeAtlas names them:
`airflow/models/dag.py` → `airflow.models.dag`, then `.Class` and `.method`. Nested functions
are not entities, so a call to a nested function is `UNKNOWN`. Put any doubt in `notes`.

## What the report gives

Each scored site gets one outcome:

| Resolver said | gold = that entity | gold = another entity | gold = `EXTERNAL`/`UNKNOWN` |
|---|---|---|---|
| confident edge | `correct` | `wrong_target` | `overclaim` |
| unconfident edge | `guess_correct` | `guess_wrong` | `guess_wrong` |
| no edge | (n/a) | `omitted` | `abstained` |

| Metric | Numerator | Denominator |
|---|---|---|
| confident precision | `correct` | all confident edges on scored sites |
| unconfident precision | `guess_correct` | all unconfident edges on scored sites |
| confident recall | `correct` | sites whose gold is an entity |
| recall, confident or not | `correct` + `guess_correct` | sites whose gold is an entity |
| omission rate | `omitted` | sites whose gold is an entity |
| correct abstention | `abstained` | sites whose gold is `EXTERNAL` or `UNKNOWN` |

Every value is printed with its numerator and denominator. Each confident error is listed by
site, because a wrong confident edge is shown to reviewers as fact.

## Limits

- A plain random sample is mostly `EXTERNAL` calls: on Airflow 1.10.7 the resolver links about
  5,100 of 21,244 call sites (24%). With 40 sites, the recall denominator may be about 10, so
  report it as a pilot with its counts, not as a general accuracy.
- Static analysis only: a `correct` edge means the source text names that target, not that it
  runs.
- Calls are matched by file, line and column, so labels only score against the same revision.
  Labels for any other position are listed as unmatched and left out.

## Status

| Item | State |
|---|---|
| Tooling and tests | done on `feature/calls-eval` |
| Airflow 1.10.7 sheet (40 sites, seed 2026, commit `c8597cbf143b970ad3c7b0d62e3b44d1dfdc8afe`) | drawn, not yet labelled. Keep it in `codeatlas-eval`, not in this repository |
| Labels, agreement, report | not started |
