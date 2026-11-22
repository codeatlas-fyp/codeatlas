# CALLS call graph

Status on branch `fix/calls-local-scope`, 18 Nov 2026. Owner: Saleha Muhammad (since the 16 Nov
handover). Original implementation: Yusra Masood, PR #53 (`feature/call-graph`, head `aac95c6`).

## What it is

`src/codeatlas/analyze/calls.py` builds a static call graph for the Python code of the repository
under evaluation. For every call it can resolve, it emits one
`CodeEdge(src, dst, kind="CALLS", confident=...)`, where both ends are entities produced by
`extract_entities` (module, class, function, method). Calls into the standard library or
third-party packages are left out, because they have no entity in the repository.

A CALLS edge means "the source text calls this function". It is a static inference. It does not
mean the call runs, that a requirement is met, or that an owner approved anything.
`confident=True` means the callee is fixed by the source text; `confident=False` means it is a
best guess and must be shown as one.

| Item | Value |
|---|---|
| Entry point | `build_call_graph(entities=..., file_sources=...)` |
| Parser | tree-sitter (`tree_sitter_python`); no tree-sitter means `[]`, never a crash |
| Extractor version | `calls@0.3.0` (PR #53: `calls@0.1.0`; phase 1: `calls@0.2.0`) |
| Wired in | `pipeline.py`, `_code()`: CALLS edges are added after IMPORTS edges and stored in the evidence bundle |
| Tests | `tests/unit/test_call_graph.py` (51 tests), `tests/unit/test_calls_verdict_invariance.py` (7 test cases) |
| Output order | sorted by (src, dst); pure function, no file I/O |

## How a call is resolved

Each call is recorded with the entity it sits in and the scope around it. Calls in a nested
function are credited to the nearest enclosing entity. Calls in parameter defaults, annotations
and decorators run when the `def` runs, so they are read in the enclosing scope and credited to
the enclosing entity (a method's default goes to its class, a function's to its module).

1. `self.m()` / `cls.m()` inside a class: the class's own method, then repository base classes,
   left-most base first (up to 4 hops).
2. A name imported inside the enclosing function (or an outer function): resolved through that
   local import.
3. A local variable (see Local scope): not resolved. `x()` gets no edge; `x.m()` may take the
   guess in step 7.
4. A module-level name with exactly one binding: resolved through it. Dotted calls such as
   `alias.Class.m()` are rewritten through the import table; re-exports through a package
   `__init__` are followed (up to 4 hops).
5. A module-level name with one real target plus assignments (see Ambiguous names): an edge to
   that target with `confident=False`.
6. Calling a class resolves to its `__init__` when it has one, else to the class.
7. Unknown receiver, `obj.m()`: an edge with `confident=False`, only if exactly one repository
   method is named `m`. Built-in method names (`get`, `append`, `read`, ...) and dunders never
   take this step, and neither does a name bound to an external import (`json.dumps`).

Recursion is dropped. If the same pair is found both confidently and as a guess, only the
confident edge is kept.

### Local scope (phase 1)

Python treats a name bound anywhere in a function as local to the whole function. The resolver
does the same. These bind a local name:

- parameters, including `*args`, `**kwargs`, typed and default parameters;
- assignment and augmented-assignment targets, including tuple and list targets
  (`obj.x = ...` and `d[k] = ...` bind nothing);
- `for` targets, `with ... as` targets, `except ... as` targets, walrus (`:=`) targets;
- nested `def` and `class` names.

A `global x` statement keeps `x` module-level. A nested function sees the locals and local
imports of the functions around it, and its own bindings hide the outer ones. An import written
inside a function binds only in that function and its nested functions.

A lambda's parameters and a comprehension's loop variables (`lambda f: f()`,
`[f() for f in fs]`) are local to the lambda or comprehension (phase 2).

### Ambiguous names (phase 2)

A name bound more than once is resolved by what its bindings are:

| Module-level bindings of `x` | Example | Result for `x()` |
|---|---|---|
| one target, any number of times | `def g` twice; `import os` twice | confident edge |
| one `def`/`class`/import target plus assignments | `try: from a import x` / `except ImportError: x = None`; `def f2` then `f2 = g`; `global x; x = ...` in a function | edge with `confident=False` |
| two different `def`/`class`/import targets | `try: from a import x` / `except: from b import x` | no edge |
| assignments only | `h = g` | no edge |

Module-level bindings are top-level `def`/`class` names, imports (also under `if`/`try`),
assignment-like targets outside functions and classes, and names a function rebinds through
`global`. Imports in a class body bind class attributes, not module names, so they no longer
count as module names.

Inside a function, a name that is both imported and assigned, or imported from two different
places, is treated as a local variable: no edge for `x()`.

## Work done after the handover

### Phase 1: local scope (SG-01), handover gaps G1 to G3

| Gap | Example | PR #53 | Now |
|---|---|---|---|
| G1 parameter shadowing | `def caller(target): target()` | `caller -> target`, confident | no edge |
| G2 local assignment shadowing | `target = print; target()` | `local -> target`, confident | no edge |
| G3 local import leaking | `def f(): from pkg.b import helper` then `def g(): helper()` | `g -> pkg.b.helper`, confident | only `f -> pkg.b.helper` |

13 tests; 9 of them fail on PR #53's code.

### Phase 2: confidence audit (SG-02), negative fixtures (SG-03), verdict check (SG-04)

| Gap | Example | Phase 1 | Now |
|---|---|---|---|
| G7 module rebinding of a `def` | `def f2(): ...` then `f2 = g`, call `f2()` | `-> f2`, confident | `-> f2`, unconfident |
| G8 conditional imports, different targets | `try: from a import x` / `except: from b import x` | `-> b.x` (last binding), confident | no edge |
| G8 optional import | `try: from a import x` / `except ImportError: x = None` | `-> a.x`, confident | `-> a.x`, unconfident |
| `global` rebinding | `def swap(): global target; target = g` | `-> target`, confident | `-> target`, unconfident |
| lambda parameter | `map(lambda f: f(), xs)` | `-> f`, confident | no edge |
| comprehension variable | `[f() for f in fs]` | `-> f`, confident | no edge |
| local name imported and assigned | `from pkg.b import helper` then `helper = print` | `-> pkg.b.helper`, confident | no edge |
| local name imported from two places | `if: from b import helper` / `else: from c import helper` | last import, confident | no edge |
| class-body import | `class H: from pkg.b import helper`, then `helper()` at module level | `-> pkg.b.helper`, confident | no edge |
| call in a default value | `def run(make=make())` | lost (the parameter hid it) | `module -> make`, confident |

New tests: 23 in `test_call_graph.py`, 11 of which fail on the phase 1 code. The others pin
behaviour that must not change or that is unsupported on purpose: the same import in both
branches stays confident, redefining the same `def` stays confident, an optional import of an
external module takes no guess, duplicate method names give no guess, a re-export cycle ends
without an edge, multiple inheritance takes the left-most base, and `super()`, `from x import *`
and `getattr(obj, "m")()` give no edge.

SG-04, `test_calls_verdict_invariance.py`:

- On the five stored demo bundles (`deploy/bundles/`, verdicts PASS, REVIEW and FAIL), adding a
  CALLS edge between every ordered pair of entities, confident or not, leaves the trace links,
  every rule result and the verdict unchanged.
- A targeted case checks the place where an edge can matter: a changed entity in another module
  becomes `DETERMINISTICALLY_DERIVED` through one confident IMPORTS edge, and the same edge as
  CALLS does not derive it. If a rule ever starts reading CALLS edges, this test fails and the
  change has to be reviewed as a rule change.

## Checks run

On a clean checkout of `feature/call-graph` (`aac95c6`) with all commits of this branch:

| Check | Result |
|---|---|
| `uv run pytest tests/unit/test_call_graph.py tests/unit/test_calls_verdict_invariance.py -q` | 58 passed |
| `uv run pytest -q` | 665 passed |
| `uv run ruff format --check src tests` | 110 files already formatted |
| `uv run ruff check src tests` | All checks passed |
| `uv run mypy src` | no issues in 59 source files |

## Results on real code

The same input was run through the three versions of `calls.py`: PR #53 (`aac95c6`), phase 1 and
phase 2.

| Code | Files | Entities | PR #53 edges (confident) | Phase 1 | Phase 2 |
|---|---|---|---|---|---|
| Apache Airflow 1.10.7 (`airflow/`, 20 Dec 2019, in the dataset window) | 627 | 4,924 | 4,179 (3,603) | 4,180 (3,603) | 4,181 (3,602) |
| CodeAtlas `src/` at `aac95c6` | 59 | 505 | 528 (501) | 528 (501) | 528 (501) |

Every edge that changed on Airflow was checked against the source:

| Change | Airflow source | Correct because |
|---|---|---|
| `TransferJobPreprocessor._inject_aws_credentials -> AwsHook.__init__` and `example_gcs_to_bq_operator -> GoogleCloudStorageToBigQueryOperator.__init__`: confident to unconfident | `try: from ... import AwsHook` / `except ImportError: AwsHook = None` | optional import; the text does not fix the callee |
| `DAG.date_range -> utcnow`, `SubDagOperator.__init__ -> SequentialExecutor.__init__`, `SafeStatsdLogger.__init__ -> AllowListValidator.__init__`: now credited to the class | `end_date=timezone.utcnow()`, `executor=SequentialExecutor()` in method defaults | defaults run when the class body runs |
| new `airflow.utils.dates -> make_aware`, confident | `def round_time(dt, delta, start_date=timezone.make_aware(datetime.min))` | module-level default |
| new `initdb -> DAG.deactivate_unknown_dags`, unconfident | `initdb` imports `models` from `airflow` and from `flask_appbuilder` | two local imports; before, the last one silently won and the edge was lost |
| phase 1: new `DAG.run -> AirflowConfigParser.getboolean`, unconfident; removed again in phase 2 | `def run(self, donot_pickle=conf.getboolean(...), ..., conf=None)` | the default reads the module's `conf`, not the parameter; `conf` is a config object, not an entity |

So on this release the fixes change few edges: the risky patterns are rare in Airflow's own
package, and none of the changes moved a confident edge to a wrong target. The fixes matter
because each pattern gives a wrong confident edge whenever it does occur, and the tests pin
those cases.

These numbers are a sanity run, not an accuracy measurement. Precision and recall on labelled
call sites are still to be measured (SG-07).

## Effect on verdicts and bundles

- No rule outcome changes (SG-04 test above). `evaluate/links.py` reads only IMPORTS edges.
- New bundles hash differently from before, because the edge set and the `calls` extractor version
  changed. Old bundles still replay: they keep the edges and version they were made with.

## Known limits (still open)

| Limit | Example | Current output |
|---|---|---|
| G4 `super()` calls | `super().run()` | no edge |
| G5 attribute chains on `self` | `self.repo.save()` | unconfident guess only, when one method is named `save` |
| G6 star imports | `from pkg.b import *` then `helper()` | no edge |
| Assignment aliases | `h = g; h()` (module or function) | no edge |
| Flow-insensitive | a name rebound later in a function is ambiguous for the whole function, even before the rebinding | unconfident or no edge |
| Local optional import | `try: from a import x` / `except: x = None` inside a function | no edge (module level gives an unconfident edge) |
| Re-exports of an ambiguous or optional name | another module imports it from here | no edge |

Not supported by design: runtime dispatch, dynamic attributes (`getattr`, `importlib`), type
inference, and any language other than Python.

## Next steps

1. SG-06: graph-view export (nodes, edges, `confident`, file, span, revision, provenance) for
   Areej's UI, in a format agreed with her.
2. SG-07: precision and recall on labelled call sites, confident and unconfident edges
   reported separately. The tooling is on `feature/calls-eval` (`resolve_call_sites`,
   `analyze/calls_eval.py`, `scripts/calls_eval.py`); see `docs/calls-evaluation.md`. The
   labels are still to be made.

## Merging

- Open the PR from `fix/calls-local-scope` into `feature/call-graph`, reviewer Yusra. Merge with
  "Create a merge commit", never squash, so the history and attribution stay intact.
- This branch changes only `calls.py`, its two test files and this file. It merges cleanly with
  `develop` and `feature/application-integration`.
- PR #53 and `feature/semantic-retrieval` (PR #54) already conflict in `pipeline.py`, `_code()`,
  with or without this branch. Resolve it by keeping both: use PR #54's four-item return type, and
  keep `edges += calls_analysis.build_call_graph(entities=entities, file_sources=sources)` before
  `return entities, changed, edges, head_sources`.
