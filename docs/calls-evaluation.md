# CALLS evaluation (SG-07)

Status on branch `feature/calls-eval` (from `feature/call-graph`), 20 Nov 2026. Owner:
Saleha Muhammad. This is the tooling only: **no accuracy figure exists yet**, because no call
sites have been labelled.

## Why

The call-graph tests show that the resolver follows the rules we wrote. They don't show how
often it is right on real code. This measures that on call sites that people label without
seeing CodeAtlas's answer, and reports confident errors apart from guesses and omissions.

## Files

| File | What it does |
|---|---|
| `src/codeatlas/analyze/calls.py` | `resolve_call_sites()`: the resolver's answer per call site. `call_site_strata()`: a sampling stratum per site from name bindings only. `build_call_graph` output is unchanged (same 4,181 edges on Airflow 1.10.7, checked 20 Nov) |
| `src/codeatlas/analyze/calls_eval.py` | pure functions: find every call site, strata, stratified sample, blind sheet, label parsing, agreement, scoring, report |
| `scripts/calls_eval.py` | command line: `sample`, `agree`, `score` (reads and writes the files) |
| `tests/unit/test_calls_eval.py` | 12 tests on a synthetic repository; they check the arithmetic and the strata, not accuracy |

## Procedure

1. **Freeze the source.** Check out the repository at a fixed commit and note the full SHA.
2. **Draw the sample**, 10 sites from each of four strata (40 in total):

   ```powershell
   uv run python scripts/calls_eval.py sample --root C:\path\to\airflow --include airflow `
       --repo apache/airflow --revision <full sha> --per-stratum 10 --seed 2026 --out sheet.csv
   ```

   This writes `sheet.csv` (for annotators) and `sheet_strata.csv` (for scoring only). Every
   call expression counts, including the ones the resolver never records (`handlers[0]()`), so
   skipped calls show up as omissions.
3. **Label blind, twice.** Two team members each fill `resolvability` and `expected_target_ids`
   in their own copy of `sheet.csv`, reading the source at that commit. They don't run CodeAtlas
   on it, look at its graph, or open the strata file.
4. **Compare.** `uv run python scripts/calls_eval.py agree --first a.csv --second b.csv` prints
   the raw agreement (resolvability and the whole target set must match) and every disagreement.
5. **Settle.** A third person decides each disagreement and writes the settled sheet. Keep both
   original sheets unchanged.
6. **Score.**

   ```powershell
   uv run python scripts/calls_eval.py score --root C:\path\to\airflow --include airflow `
       --labels settled.csv --strata sheet_strata.csv --out report.md
   ```

## Strata

Strata spread the 40 sites over different kinds of call, so the sample isn't almost all
standard-library calls. They come from **name bindings only, never from the resolver's answer**,
and they are kept out of the annotators' sheet because a stratum would hint at the answer.

| Stratum | Call sites |
|---|---|
| `local` | `self.m()` / `cls.m()`; names bound to a def, class or import in the same module |
| `cross_file` | names bound to another repository module |
| `ambiguous_dynamic` | parameters and local variables, names bound more than once, `self.x.m()` chains, `super()`, calls the resolver never records (`x[0]()`, `f()()`), unbound receivers |
| `external` | names bound to a module outside the repository, builtins, methods on string literals |

On Airflow 1.10.7 (`airflow/`, 21,244 call sites): local 2,684, cross_file 2,785,
ambiguous_dynamic 8,149, external 7,626 (20 Nov). A stratum with fewer than 10 sites gives a
shortfall line instead of filling up from other strata.

## Labelling rules

| `resolvability` | When | `expected_target_ids` |
|---|---|---|
| `resolvable` | the call runs code defined in the repository | every repository entity it can run, separated by `;` (usually one). A class call is the class's `__init__` if the class defines one (`pkg.mod.Class.__init__`), else the class (`pkg.mod.Class`) |
| `external` | it runs code outside the repository: standard library, third-party, builtin | empty |
| `dynamic_unknown` | the source text alone does not decide it, even for a person: a callback passed in, `getattr(obj, name)()`, a receiver of unknown type | empty |
| `skip` | not a real call site (rare) | empty |

Entity ids are dotted module paths from the repository root, as CodeAtlas names them:
`airflow/models/dag.py` → `airflow.models.dag`, then `.Class` and `.method`. Nested functions
are not entities, so a call to a nested function is `dynamic_unknown`. Use several targets only
when the source text allows each of them (e.g. two branches bind the name to different
functions). Put any doubt in `notes`. Sheets from the first version (one `gold` column) are
still read: an entity id means `resolvable`, `EXTERNAL` means `external`, `UNKNOWN` means
`dynamic_unknown`.

## What the report gives

Each scored site gets one outcome:

| Resolver said | target in the expected set | resolvable, other target | `external` / `dynamic_unknown` |
|---|---|---|---|
| confident edge | `correct` | `wrong_target` | `overclaim` |
| unconfident edge | `guess_correct` | `guess_wrong` | `guess_wrong` |
| no edge | (n/a) | `omitted` | `abstained` |

A `dynamic_unknown` site with no edge counts as `abstained`, never as a known miss.

Site-level metrics, each with numerator and denominator:

| Metric | Numerator | Denominator |
|---|---|---|
| confident precision | `correct` | all confident edges |
| confident recall | `correct` | resolvable sites |
| confident F1 | 2 × `correct` | 2 × `correct` + confident errors + resolvable sites without a correct confident edge |
| false-confident rate | `wrong_target` + `overclaim` | all scored sites |
| unconfident precision | `guess_correct` | all unconfident edges |
| recall, confident or not | `correct` + `guess_correct` | resolvable sites |
| omission rate | `omitted` | resolvable sites |
| unresolved rate | `omitted` + `abstained` | all scored sites |
| correct abstention | `abstained` | `external` + `dynamic_unknown` sites |

Edge-level counts (TP, FP, FN) are given for confident edges and for all edges, because one
resolvable site can have several expected targets while the resolver gives at most one. The
report also breaks the outcomes down per stratum and lists every confident error, wrong guess
and omission by site.

## Limits

- 40 sites is a pilot: report each value with its counts, not as a general accuracy.
- Strata change the mix of the sample on purpose, so the overall rates describe this sample,
  not the repository. Read them per stratum.
- Static analysis only: a `correct` edge means the source text names that target, not that it
  runs.
- Calls are matched by file, line and column, so labels only score against the same revision.
  Labels for any other position are listed as unmatched and left out.

## Status

| Item | State |
|---|---|
| Tooling and tests | done on `feature/calls-eval` |
| Airflow 1.10.7 sheet (40 sites, 10 per stratum, seed 2026, commit `c8597cbf143b970ad3c7b0d62e3b44d1dfdc8afe`) | drawn 20 Nov, not yet labelled. Keep the sheet and the strata file in `codeatlas-eval`, not in this repository |
| Labels, agreement, report | not started |
