# G1 checkpoint: schema and rule interface frozen

- **Date:** 2026-10-07
- **Gate (spec §9):** `schema/` models, `WorkItemSource`, `ChangeSource` and `Rule` protocols
  merged; one sample bundle under `tests/fixtures/bundles/`; replay of that bundle byte-identical
  in CI.
- **Status:** ready for the `g1` tag **once PRs #5, #6, #7, #9 and the step 4 PR are merged into
  `develop`** (in that order, with merge commits). Tagging is the owner's job; the agent may not
  tag.

## What is frozen at G1

After the tag, any change below needs an issue, a `schema_version` note, and approval from all
three members (spec §7, §9).

| Contract | Where | Defined in |
|---|---|---|
| Domain models (schema v0) and their field types | `src/codeatlas/schema/` | step 2 spec, spec §7.1 |
| Approved additions S1–S6 (account-id approvers, `Adr`, bundle `collected_at`/`run_id`/`policy`/`identity_map`/`model_file_hashes`, `labels`, `unresolved_fields`, lifecycle `evidence_id`s) | `schema/` | step 2 spec, "Decisions" |
| `WorkItemSource`, `ChangeSource` | `schema/protocols.py` | spec §7.2 |
| `Rule` protocol (`case`, `version`, `required_evidence`, `check`) | `evaluate/rules/base.py` | step 4 spec |
| Canonical form (sorting identifiers, UTC seconds, 4-decimal float strings) | `bundle/canonical.py` | step 3 spec |
| `bundle_hash`, `verdict_hash` formulas | `bundle/hashing.py` | step 3 spec, spec §7.4 |
| Verdict truth table | `evaluate/verdict.py` | spec §7.4, step 4 spec |
| Store layout `<hash>/bundle.json`, `<hash>/verdict.json` | `bundle/store.py` | step 3 spec |
| Exit codes 3/4/5 on error classes | `errors.py` | step 3 spec |
| Layer contracts K1–K4 | `.importlinter` | step 1 spec |

Not frozen: rule implementations (step 7 onward), collectors, the CLI, the API.

## Sample bundle

| Item | Value |
|---|---|
| Folder | `tests/fixtures/bundles/cb973272d317b0a5e4d2f5b2198bf53740bac9ab2c93d07d581a51ff40ea4f66/` |
| `bundle_hash` | `cb973272d317b0a5e4d2f5b2198bf53740bac9ab2c93d07d581a51ff40ea4f66` |
| Verdict | `PASS`, `ruleset_version` `none` (no rules before step 7) |
| `verdict_hash` | `8a04ba3de298471d9b4c9a7686dc88de78f2db44260934b1cf27e19a29afd15f` |
| Produced by | `scripts/make_sample_bundle.py` (schema objects in code, fake identities) |

## Replay result

Local (Windows 10, Python 3.12):

```
ReplayResult(bundle_hash='cb973272…ea4f66',
             stored_verdict_hash='8a04ba3d…afd15f',
             recomputed_verdict_hash='8a04ba3d…afd15f',
             match=True)
```

CI (GitHub Actions, ubuntu-latest): the same tests run in the `ci` job of each PR.
`test_ac4_sample_bundle_matches_golden_hash` checks that Linux computes the hash produced on
Windows, and `test_ac7_sample_bundle_replays_with_the_default_rules` replays the stored verdict.
See the "Replay in CI" line below.

**Replay in CI:** passed. PR #9, run 37624307090 (ubuntu-latest): 181 passed, including the golden-hash test. PR #10, run 37624510938 (ubuntu-latest): 200 passed, including `test_ac7_sample_bundle_replays_with_the_default_rules` (replay `match=True` on Linux).

## Known gaps at G1

- The sample bundle is generated from code, not recorded (no recorder before step 5; reconciliation
  F12). Recorded bundles follow after step 8.
- There is no separate "determinism" CI job yet (step 15, Saleha); the golden-hash and replay
  tests above stand in for it.
- `Adr` fields still need Yusra's approval on PR #6.
