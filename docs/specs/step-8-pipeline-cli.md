# Step 8: Pipeline skeleton and CLI

- **Owner:** Areej (@areej8)
- **Step:** 8 of the mid-evaluation spec (section 10)
- **Prerequisites:** step 3 (bundle), step 5 (Jira collector); uses steps 6 and 7
- **Spec sections:** 5 (stages, exit codes, never partial), 7.7 (CLI), 15 (config, logging,
  errors), 16 (secrets in logs), 17 (AC01–AC05, AC12)
- **Branch:** `feature/pipeline-cli`, stacked on `feature/rules`

## Purpose

Run the requirement half of the pipeline end to end: collect Jira evidence for the keys a change
names, freeze it into a bundle, evaluate it, store bundle and verdict, and exit with a code CI can
gate on. Everything after the freeze works offline.

## The change file (stand-in for GitHub and git evidence)

The GitHub collector and git adapter are Yusra's (step 9) and do not exist yet. Until then the
change under evaluation is described by a JSON file, validated on load:

```json
{
  "repo": "codeatlas-fyp/codeatlas-sandbox", "pr_number": 7,
  "base_sha": "...", "head_sha": "...", "merge_base_sha": "...",
  "work_items": [{"key": "SBX-7", "found_in": "pr_title", "commit_sha": null}],
  "commits": [{"sha": "...", "author_email": "user-02@example.test", "author_login": "user-02",
               "committed_at": "2026-10-08T09:00:00Z", "message": "SBX-7: renew a loan"}]
}
```

It is turned into schema objects (`ChangeRef`, `WorkItemRef`, `Commit`) with `source` `github`
or `git` and `source_ref` `change-file:<path>`, so the bundle says where the evidence came from.
This is a stated limitation of the mid-evaluation build: implementation times (C1, C6) come from
this file, not from GitHub.

## Stages run (spec §5)

| # | Stage | Here |
|---|---|---|
| 1 | resolve change | from the change file |
| 2 | load policy | `--policy` YAML, else built-in defaults recorded as a collection note (§5) |
| 3 | work-item keys | from the change file (`analyze.keys` is Yusra's) |
| 4 | collect requirements | `JiraClient` (live) or `FixtureJiraSource` (`--fixtures DIR`); a missing issue becomes the note `work item <KEY> not found`; Jira unreachable → exit 3, no bundle |
| 5 | lifecycle | `analyze.lifecycle.reconstruct` with `adf_to_text`/`wiki_to_text`; approvers = account ids ∪ readable group members; an unreadable group with no ids → note `approvers unknown: <KEY>` |
| 6–10 | governance, reviews, code, criteria, semantic | not collected (Yusra); empty lists and the note `governance, reviews, code and semantic evidence not collected (steps 9-13)` |
| 11 | freeze | canonical bundle, `bundle_hash`, store |
| 12–13 | links, rules, verdict | `pipeline.evaluate_bundle` with `default_rules()` |

## CLI (`codeatlas`)

| Command | Does | Exit code |
|---|---|---|
| `collect --change FILE [--policy FILE] [--identity-map FILE] [--fixtures DIR] [--store DIR]` | stages 1–13; prints bundle hash, verdict, each result | verdict code |
| `evaluate --bundle PATH` | stages 12–13 on a stored bundle, offline; stores the verdict | verdict code |
| `replay PATH` | recompute and compare `verdict_hash` | 0 match, 5 mismatch or tampered |
| `show PATH [--json]` | print the stored bundle's verdict and results | 0 |
| `version` | print the version | 0 |

Exit codes (§5): 0 PASS, 1 FAIL, 2 REVIEW or UNKNOWN, 3 collection error, 4 invalid evidence or
config, 5 replay mismatch.

Decision (reconciliation F16): §7.7's `evaluate --repo --github --pr` needs the GitHub collector,
so this step implements the command forms §10 asks for (`collect`, `evaluate --bundle`, `replay`)
plus `show` and `version`.

## Configuration (`config.py`) and logging (`logging.py`)

- `Settings` (pydantic-settings) reads `CODEATLAS_JIRA_BASE_URL`, `CODEATLAS_JIRA_EMAIL`,
  `CODEATLAS_JIRA_TOKEN`, `CODEATLAS_BUNDLE_DIR` (default `bundles`) and `CODEATLAS_LOG_LEVEL`
  from the environment and `.env`. Missing Jira settings for a live run → `ConfigError`, exit 4,
  naming the missing key.
- `load_policy`, `load_identity_map` and `load_change` use `yaml.safe_load` or JSON and schema
  validation; any error → `ConfigError` (exit 4).
- Logs are JSON lines to stderr with `ts`, `level`, `event`, `run_id`, `stage`, `duration_ms`.
  Every stage logs start and end. A redaction filter replaces the value of any field whose name
  contains `token`, `authorization`, `password` or `secret`, and every email address, with
  `[redacted]`.

## Acceptance criteria

- **AC1 (spec AC01)** Given the recorded SBX fixtures and a change file, `collect` writes a bundle
  whose `bundle_hash` equals the hash recomputed from its bytes, and a verdict next to it.
- **AC2 (spec AC02)** Evaluating a stored bundle twice in separate processes gives the same
  `verdict_hash`.
- **AC3 (spec AC03)** A stored bundle with one byte changed: `replay` exits 5; no verdict is
  written.
- **AC4 (spec AC04)** `evaluate --bundle` succeeds with the network blocked.
- **AC5 (spec AC05)** Jira unreachable (connection error): `collect` exits 3, writes no bundle,
  and the log contains `SourceUnavailable` but not the token.
- **AC6** A wrong token (401): `collect` exits 3, no bundle.
- **AC7 (spec AC12)** A change file with no work items: verdict UNKNOWN, exit 2.
- **AC8** Exit codes: PASS 0, FAIL 1, REVIEW 2, UNKNOWN 2; invalid policy YAML 4; missing Jira
  settings for a live run 4.
- **AC9** The logger redacts a token passed in an event field and an email inside a message.
- **AC10** `show` prints the verdict and every case result; `--json` prints the stored verdict
  JSON.
- **AC11** A missing issue (404) is a collection note, not a crash; C3 for that key is
  INSUFFICIENT_EVIDENCE.

## Fixtures

| Fixture | Expected |
|---|---|
| `tests/fixtures/jira/SBX-*` (recorded) | collected through `FixtureJiraSource` |
| `config/policy.example.yaml` (fake account ids) | loads into `Policy` |
| `config/identity_map.example.yaml` (fake values) | loads into `IdentityMap` |
| change files | written by the tests into `tmp_path` from schema-shaped dicts |

## Files to touch

- `src/codeatlas/{config,logging,pipeline,cli}.py`
- `config/policy.example.yaml`, `config/identity_map.example.yaml`
- `tests/unit/test_config.py`, `tests/unit/test_logging.py`, `tests/integration/test_cli.py`
- `.importlinter` (add `codeatlas.config`, `codeatlas.logging` to the forbidden lists of K1/K4)

## Out of scope

- GitHub, git, governance, code analysis and semantic stages (Yusra), the API and web UI
  (Saleha).
