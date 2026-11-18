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

| Item | Value |
|---|---|
| Entry point | `build_call_graph(entities=..., file_sources=...)` |
| Parser | tree-sitter (`tree_sitter_python`); no tree-sitter means `[]`, never a crash |
| Extractor version | `calls@0.2.0` (was `calls@0.1.0` before the local-scope fix) |
| Wired in | `pipeline.py`, `_code()`: CALLS edges are added after IMPORTS edges and stored in the evidence bundle |
| Tests | `tests/unit/test_call_graph.py`, 28 tests |
| Output order | sorted by (src, dst); pure function, no file I/O |

## How a call is resolved

Each call is recorded with the entity it sits in (calls in a nested function are credited to the
nearest enclosing entity) and with the local scope of the function(s) around it.

1. `self.m()` / `cls.m()` inside a class: the class's own method, then repository base classes
   (up to 4 hops).
2. A name imported inside the enclosing function (or an outer function): resolved through that
   local import.
3. A local variable (see below): not resolved. `x()` gets no edge; `x.m()` may still take the
   unconfident guess in step 6.
4. A module-level name: a top-level `def`/`class` or a module-level import (including imports
   under `if`/`try` and in class bodies). Dotted calls such as `alias.Class.m()` are rewritten
   through the import table. Re-exports through a package `__init__` are followed (up to 4 hops).
5. Calling a class resolves to its `__init__` when it has one, else to the class.
6. Unknown receiver, `obj.m()`: an edge with `confident=False`, only if exactly one repository
   method is named `m`. Built-in method names (`get`, `append`, `read`, ...) and dunders never
   take this step.

Steps 1, 2, 4 and 5 give `confident=True`. Recursion is dropped. If the same pair is found both
confidently and as a guess, only the confident edge is kept.

### Local scope (added in this branch)

Python treats a name bound anywhere in a function as local to the whole function. The resolver
now does the same. These bind a local name:

- parameters, including `*args`, `**kwargs`, typed and default parameters;
- assignment and augmented-assignment targets, including tuple and list targets
  (`obj.x = ...` and `d[k] = ...` bind nothing);
- `for` targets, `with ... as` targets, `except ... as` targets, walrus (`:=`) targets;
- nested `def` and `class` names.

A `global x` statement keeps `x` module-level. Names bound inside a lambda or a comprehension
belong to that scope and are ignored. A nested function sees the locals and local imports of the
functions around it, and its own bindings hide the outer ones.

An import written inside a function now binds only in that function and its nested functions. It
no longer leaks to the rest of the module.

## What changed in this branch (SG-01)

Two commits on top of PR #53:

| Commit | Change |
|---|---|
| `test(analyze): pin local-scope shadowing and local imports in the call graph` | 13 new tests. 9 failed on the old code: they reproduce handover gaps G1 to G3. The other 4 are controls that must not change. |
| `fix(analyze): resolve calls through local scope, never shadowed module names` | `_Scope` and `_function_scope` track local names and local imports per function; `_Resolver.resolve` checks them before module names; module-level import collection skips function bodies; extractor version `calls@0.2.0`. |

Handover gaps fixed:

| Gap | Example | Before | After |
|---|---|---|---|
| G1 parameter shadowing | `def caller(target): target()` | `caller -> target`, confident | no edge |
| G2 local assignment shadowing | `target = print; target()` | `local -> target`, confident | no edge |
| G3 local import leaking | `def f(): from pkg.b import helper` then `def g(): helper()` | `g -> pkg.b.helper`, confident | only `f -> pkg.b.helper` |

New tests (all in `tests/unit/test_call_graph.py`):

- `test_parameter_shadows_global_function_gives_no_edge`
- `test_star_and_keyword_parameters_shadow_globals`
- `test_local_assignment_shadows_global_function`
- `test_loop_with_except_and_walrus_targets_shadow_globals`
- `test_assignment_in_tuple_target_shadows_global`
- `test_nested_function_name_shadows_global`
- `test_closure_sees_enclosing_parameter`
- `test_function_local_import_does_not_leak_to_other_functions`
- `test_local_import_shadows_module_level_name`
- controls: `test_global_statement_keeps_module_binding`,
  `test_attribute_assignment_does_not_shadow`,
  `test_function_local_import_resolves_inside_nested_function`,
  `test_parameter_receiver_still_uses_unique_method_guess`

## Checks run

On a clean checkout of `feature/call-graph` (`aac95c6`) with these two commits:

| Check | Result |
|---|---|
| `uv run pytest tests/unit/test_call_graph.py -q` | 28 passed (9 of the new tests fail without the fix) |
| `uv run pytest -q` | 635 passed |
| `uv run ruff format --check src tests` | 109 files already formatted |
| `uv run ruff check src tests` | All checks passed |
| `uv run mypy src` | no issues in 59 source files |

Sanity run on CodeAtlas's own `src/`: the old code gives 505 entities, 528 CALLS edges, 501
confident (the numbers reported on PR #53). With the fix, no edge changes outside `calls.py`
itself; the shadowing patterns do not occur in CodeAtlas's own code. They do occur in real
repositories, which is why the fix matters for the evaluation dataset.

These are unit tests and a sanity run, not a measurement of accuracy. Precision and recall on
labelled call sites are still to be measured (SG-07).

## Effect on verdicts and bundles

- No rule outcome changes. `evaluate/links.py` reads only IMPORTS edges; CALLS edges are stored
  in the bundle but no rule uses them yet.
- New bundles hash differently from before, because the edge set and the `calls` extractor version
  changed. Old bundles still replay: they keep the edges and version they were made with.

## Known limits (still open)

Checked by running `calls.py` on this branch:

| Gap | Example | Current output | Risk |
|---|---|---|---|
| G4 | `super().run()` | no edge | missed edge |
| G5 | `self.repo.save()` | unconfident guess only, when one method is named `save` | marked as a guess |
| G6 | `from pkg.b import *` then `helper()` | no edge | missed edge |
| G7 | `h = g; h()` inside a function, or `h = g` at module level | no edge | missed edge |
| G7 | module-level `f2 = g` where `f2` is also a top-level `def` | `f -> f2`, **confident** | wrong confident edge |
| G8 | `try: from pkg.a import x` / `except: from pkg.b import x` | `f -> pkg.b.x` (last binding), **confident** | possibly wrong confident edge |

Not supported by design: runtime dispatch, dynamic attributes (`getattr`, `importlib`), type
inference, and any language other than Python.

## Next steps

1. SG-03: make G7 (module-level rebinding) and G8 (conditional imports) give no edge or an
   unconfident one, with tests; add more negative fixtures (duplicate method names, re-export
   cycles, inheritance ambiguity).
2. SG-06: graph-view export (nodes, edges, `confident`, file, span, revision, provenance) for
   Areej's UI, in a format agreed with her.
3. SG-07: a labelled call-site fixture and precision/recall for confident and unconfident edges,
   reported separately.

## Merging

- Open the PR from `fix/calls-local-scope` into `feature/call-graph`, reviewer Yusra. Merge with
  "Create a merge commit", never squash, so the history and attribution stay intact.
- This branch only changes `calls.py`, its tests and this file. It merges cleanly with `develop`
  and `feature/application-integration`.
- PR #53 and `feature/semantic-retrieval` (PR #54) already conflict in `pipeline.py`, `_code()`,
  with or without this branch. Resolve it by keeping both: use PR #54's four-item return type, and
  keep `edges += calls_analysis.build_call_graph(entities=entities, file_sources=sources)` before
  `return entities, changed, edges, head_sources`.
