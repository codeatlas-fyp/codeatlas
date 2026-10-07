# Step 3: Canonical form, hashing, store and replay

- **Owner:** Areej (@areej8)
- **Step:** 3 of the mid-evaluation spec (section 10). Gate G1 depends on it.
- **Prerequisites:** step 2 (`schema/`)
- **Spec sections:** 5 (never partial, exit codes), 7.4 (hashes, canonical form), 7.8 and 15
  (errors), 13 (determinism), 16 (bundle integrity)
- **Branch:** `feature/bundle-replay`, stacked on `feature/schema-bundle`

## Purpose

Freeze evidence into bytes that are identical on every machine, name them by their SHA-256, and
prove later that a stored verdict still follows from those bytes. This is the "replayable"
contribution (spec §1, point 4).

## Inputs and outputs

| Function | Input | Output |
|---|---|---|
| `bundle.canonical.canonical_bytes(obj)` | any schema model, or a dict/list of them | `bytes` (canonical JSON, §7.4) |
| `bundle.hashing.bundle_hash(bundle)` | `EvidenceBundle` | 64-char hex SHA-256 of `canonical_bytes(bundle)` |
| `bundle.hashing.verdict_hash(bundle_hash, ruleset_version, results, value)` | `str`, `str`, `list[CheckResult]`, `VerdictValue` | 64-char hex |
| `bundle.store.BundleStore(root).save(bundle)` | `EvidenceBundle` | `bundle_hash`; writes `<root>/<hash>/bundle.json` |
| `BundleStore.save_verdict(verdict)` | `Verdict` | writes `<root>/<bundle_hash>/verdict.json` |
| `bundle.store.load_bundle(path)` | path to `bundle.json` or its folder | `EvidenceBundle`, after integrity checks |
| `bundle.replay.replay(path, evaluate)` | bundle folder; `evaluate: Callable[[EvidenceBundle], Verdict]` | `ReplayResult(stored_verdict_hash, recomputed_verdict_hash, match)` |

`replay` receives the evaluator by injection: `bundle` may import only `schema` (§4), and the real
evaluator (`evaluate.verdict`) arrives in step 4.

## Canonical form (§7.4, made precise)

1. UTF-8 JSON, keys sorted, separators `,` and `:` with no spaces, no trailing newline.
2. Datetimes: converted to UTC, truncated to the second, written `YYYY-MM-DDTHH:MM:SSZ`.
3. Floats: rounded to 4 decimals and written as strings (`"0.0328"`).
4. Tuples keep their order (`hunk_lines`, each `assignee_history` entry).
5. Every list is sorted by its element's identifier:

| Element | Identifier |
|---|---|
| any `Evidence` subclass, `Approval`, `PriorityChange` | `evidence_id` |
| `RequirementVersion` | `(key, version_no)` |
| `LifecycleFacts` | `key` |
| `ResolvedPerson` | `(git_email, github_login, jira_account_id)`, `None` as `""` |
| `OwnerRule` | `line` (keeps CODEOWNERS order, which "last match wins" depends on) |
| `Adr` | `adr_id` |
| `CodeEntity` | `entity_id` |
| `CodeEdge` | `(src, dst, kind)` |
| `Criterion` | `criterion_id` |
| `SemanticCandidate` | `(criterion_id, fused_rank, entity_id)` |
| `TraceLink` | `(requirement_key, criterion_id or "", entity_id)` |
| `CheckResult` | `case` |
| `IdentityEntry` | `jira_account_id` |
| strings, numbers | their own value |
| tuples (e.g. `assignee_history` entries) | their canonical values, element by element |

## Hashes (§7.4)

- `bundle_hash = SHA256(canonical_bytes(bundle))`. The bundle has no `bundle_hash` field (step 2,
  S3), so "bundle without bundle_hash" holds by construction.
- `verdict_hash = SHA256(bundle_hash ‖ ruleset_version ‖ canonical_bytes({"results": results,
  "value": value}))`, where `‖` is plain byte concatenation of the UTF-8 strings.

## Store layout and integrity

```
<root>/<bundle_hash>/bundle.json    canonical bytes of the bundle
<root>/<bundle_hash>/verdict.json   canonical bytes of the verdict (optional)
```

- Writes go to a temporary file in the same folder, then `os.replace`, so a crash never leaves a
  half-written file (§5 "never partial").
- `load_bundle` refuses with `ReplayMismatchError` (exit 5) when:
  - the file's SHA-256 differs from its folder name;
  - the file is not valid JSON or fails schema validation;
  - the bytes are not already canonical.
- `replay` also refuses when `verdict.json` names a different `bundle_hash`, and reports
  `match=False` (raising `ReplayMismatchError`) when the recomputed `verdict_hash` differs.

## Errors (`src/codeatlas/errors.py`)

§7.8 and §15 name the same errors differently (reconciliation F17). Decision: §7.8 names are the
classes; §15 names are aliases; each class carries its exit code.

| Class | Alias | Exit code |
|---|---|---|
| `CodeAtlasError` | | 1 is never used for errors; subclasses set their own |
| `CollectionError(source, status, retryable)` | | 3 |
| `SourceUnavailable(CollectionError)` | | 3 |
| `ConfigError` | | 4 |
| `EvidenceValidationError` | `EvidenceInvalid` | 4 |
| `ReplayMismatchError` | `ReplayMismatch` | 5 |

## Acceptance criteria

- **AC1** Given a bundle, when `canonical_bytes` runs twice in separate processes, then the bytes
  are identical.
- **AC2 (property)** Given a bundle whose lists are shuffled and whose datetimes use a non-UTC
  offset, then `canonical_bytes` equals that of the original.
- **AC3 (property)** Given any bundle, `canonical_bytes(load(canonical_bytes(b)))` equals
  `canonical_bytes(b)` (idempotent), and the same bundle always gives the same `bundle_hash`.
- **AC4** Given the committed sample bundle, then its `bundle_hash` equals the golden value
  written in the test. CI on Linux and local runs on Windows must agree: this is the "2 OS
  images" check (§10 step 3).
- **AC5** Given a stored bundle with one byte changed, when it is loaded or replayed, then
  `ReplayMismatchError` is raised and its exit code is 5.
- **AC6** Given a stored bundle and verdict, when replayed with the same evaluator, then
  `match` is true and both hashes are equal. With an evaluator that returns a different value,
  `ReplayMismatchError` is raised.
- **AC7** Given a save that fails part-way (simulated), then no `bundle.json` exists in the
  store.
- **AC8** Floats are strings with 4 decimals; datetimes end in `Z` with no fraction; there are no
  spaces outside string values.
- **AC9** Every model that can appear inside a list has an identifier in the table above (guard
  test, so a new model cannot be added without one).

## Fixtures (expected results written before code)

| Fixture | Produced by | Expected |
|---|---|---|
| `tests/fixtures/bundles/sample/bundle.json` | `scripts/make_sample_bundle.py` (schema objects in code, deterministic) | loads; `bundle_hash` = golden value in `test_bundle.py`; folder renamed to its hash by the script |

Decision (reconciliation F12): the G1 sample bundle cannot come from the recorder, because the
recorder (step 5) arrives after G1 and the GitHub collector is Yusra's step 9. It is generated
from code by a committed script, never typed by hand, and uses only fake identities. It will be
joined by recorded bundles after step 8.

## Files to touch

- `src/codeatlas/errors.py`
- `src/codeatlas/bundle/{__init__,canonical,hashing,store,replay}.py`
- `scripts/make_sample_bundle.py`
- `tests/unit/test_bundle.py`, `tests/property/test_bundle_properties.py`
- `tests/fixtures/bundles/<hash>/bundle.json`

## Out of scope

- The verdict truth table and real evaluator (step 4); `verdict.json` for the sample (G1).
- The CLI `replay` command and exit-code mapping (step 8).
- A CI job running two OS images (step 15, Saleha); AC4's golden hash covers it until then.

## Decisions made while implementing

- `replay(path, evaluate, strict=True)`: with `strict=False` it returns `match=False` instead of
  raising, so the API's `/replay` endpoint (step 14) can report a mismatch as data.
- The evaluator signature is `(bundle, bundle_hash) -> Verdict`, because the verdict carries the
  hash of the bundle it judged.
- List sort key: the identifier first, then the element's full canonical text, so two elements
  with the same identifier still sort the same way whatever the input order.
- `.gitignore` now ignores `/bundles/` (the run-time store at the repo root) instead of every
  `bundles/` folder, which had hidden `tests/fixtures/bundles/`.
- Bundle fixtures are excluded from the whitespace hooks (`.pre-commit-config.yaml`) and marked
  `-text` (`.gitattributes`): the end-of-file fixer had added a newline, which changed the bytes
  and so the hash.
- Sample bundle hash: `cb973272d317b0a5e4d2f5b2198bf53740bac9ab2c93d07d581a51ff40ea4f66`.

## Mutation check

| File | Mutation | Result |
|---|---|---|
| `bundle/canonical.py` | lists no longer sorted | 1 failed (AC2 property: shuffled list order changes the bytes) |
| `bundle/store.py` | hash-vs-folder check disabled | 1 failed (AC5: tampered byte accepted) |
| `bundle/hashing.py` | `ruleset_version` dropped from `verdict_hash` | 1 failed (verdict hash ignores the ruleset) |
| `bundle/canonical.py` | datetimes written with `isoformat()` | 1 failed (AC2/AC8 format) |

Branch coverage: `bundle/` and `errors.py` 95% (161 statements, 44 branches). Property tests run
200 examples each (AC2, AC3).
