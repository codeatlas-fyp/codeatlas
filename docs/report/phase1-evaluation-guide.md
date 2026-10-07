# Phase 1 evaluation guide (for Areej)

How to check, by yourself, everything built in steps 2–8. All commands run from the repository
root in Git Bash, with `uv` installed.

```bash
uv sync                       # install everything
uv run pytest -q              # the whole test suite (network blocked)
```

## 0. Results at a glance (from `docs/report/phase1-evaluation.md`)

- **50 generated SBX issues** (SBX-10 to SBX-59, label `gen-2026-50`, seed 2026):
  - version reconstruction exact match **50 / 50**;
  - C1, C3, C6 and C8 (both policies) **50 / 50 each, 0 false PASS, 0 false FAIL**;
  - expected violations: C1 25, C3 41 (29 never approved, 12 stale), C6 19, C8 20.
- **Determinism** 10 / 10 bundles (two separate processes, same `verdict_hash`); **tamper**
  test exit code 5.
- **Branch coverage:** `schema/` 100%, `evaluate/` 100%, `analyze/lifecycle.py` 98.8%,
  `bundle/` 95.5%, `collect/jira/` 89.5%.
- **Mutation score** 97 / 103 (94.2%); the 6 survivors are equivalent mutants.
- **Property tests:** 7 properties, 1,900 generated examples.

**How to read the 100%.** The oracle (`tools/scenario_generator/oracle.py`) is written separately
from the rules and replays what the generator actually did in Jira, with Jira's timestamps.
Agreement on 50 issues with both outcomes present for every case shows that the implementation
does what the spec's rules say. It does not show that the rules are the right rules; that is a
question for the panel and the spec. C8 "with authority" has no expected violation by
construction (the generator's account is then an authority).

Two trial issues, SBX-8 and SBX-9 (label `gen-9001-2`), were created while testing the generator;
they are not part of the evaluation.

## 1. What each feature does

| Feature | In plain words | Example |
|---|---|---|
| **Schema v0** (`src/codeatlas/schema/`) | The shared vocabulary: every fact (a Jira change, a commit, a version, a verdict) is a typed, frozen object that rejects unknown fields and naive times. | A `ChangeEvent` says "history 10093 changed `description` on SBX-6 at 11:27:48Z, by user-01". |
| **Canonical bundle and hashes** (`bundle/`) | All evidence for one evaluation is frozen into one JSON file whose bytes are identical on every machine; its SHA-256 is its name. | The sample bundle is `cb973272…ea4f66` on Windows and on Linux CI. |
| **Replay** (`bundle/replay.py`, `codeatlas replay`) | Recompute a stored verdict from its bundle and prove it is identical; refuse if one byte changed. | Tampered bundle → exit code 5. |
| **Rule framework and verdict** (`evaluate/`) | Each case is a pure function; a fixed truth table turns results into PASS/FAIL/REVIEW/UNKNOWN. | Any block-severity violation → FAIL, whatever the other rules say. |
| **Jira collector** (`collect/jira/`) | Read-only client: issue, full changelog, comments, groups, status categories; retries on 429/5xx, maps every failure to exit code 3. | A wrong token → exit 3, no bundle written. |
| **ADF and wiki text** (`collect/jira/adf.py`) | Jira's current text is ADF, its history is wiki markup; both become the same plain text so history can be checked. | `h2. Acceptance criteria` and an ADF heading both become `Acceptance criteria`. |
| **Recorder and sanitiser** (`collect/jira/recorder.py`) | Records real responses as test fixtures, with people replaced by `user-01`… | `tests/fixtures/jira/SBX-6.changelog.json`. |
| **Lifecycle** (`analyze/lifecycle.py`) | Rebuilds every version of a requirement from its changelog, checks the history chains, finds approvals, revocations, priority changes and assignees. | SBX-6 → 4 versions. |
| **C3** | Requirement not approved, or approval stale (edited after approval). Severity block → FAIL. | Approved, then description edited → "stale approval". |
| **C1** | Requirement edited after the first commit → REVIEW. | Edit after the PR started. |
| **C6** | Requirement edited after the last commit (code implements an old version) → REVIEW. | Edit after the PR was finished. |
| **C8** | Priority changed after approval by someone without authority → REVIEW. | Priority raised by a non-authority. |
| **C7 stub** | Asks for test evidence when the label `requires-test-evidence` is present → UNKNOWN until Yusra's full rule. | Labelled issue → INSUFFICIENT_EVIDENCE. |
| **Pipeline and CLI** (`pipeline.py`, `cli.py`) | `collect`, `evaluate`, `replay`, `show`, `version`, with exit codes 0–5. | `codeatlas collect …` → bundle + verdict. |
| **Scenario generator** (`tools/scenario_generator/`) | Creates seeded SBX issues labelled `generated` and records what it did as ground truth. | 50 issues with random edits, approvals, priorities. |
| **Evaluation** (`scripts/evaluate_phase1.py`) | Runs CodeAtlas on the recorded and generated issues and writes `docs/report/phase1-evaluation.md`. | Accuracy per case, false PASS/FAIL. |

## 2. Try each feature yourself

Make a change file (it stands in for GitHub data until Yusra's collector exists):

```bash
mkdir -p .recordings/try
cat > .recordings/try/change.json <<'EOF'
{"repo": "codeatlas-fyp/codeatlas-sandbox", "pr_number": 6, "base_sha": "aaaa",
 "head_sha": "bbbb", "merge_base_sha": "cccc",
 "work_items": [{"key": "SBX-6", "found_in": "pr_title"}],
 "commits": [{"sha": "1111", "author_email": "user-02@example.test", "author_login": "user-02",
              "committed_at": "2026-10-07T12:00:00Z", "message": "SBX-6: register members"}]}
EOF
```

Put your real account id in a local policy (never commit it):

```bash
cp config/policy.example.yaml .recordings/try/policy.yaml
# edit .recordings/try/policy.yaml: replace user-01 with your Jira accountId (both places)
```

| Check | Command | What you should see |
|---|---|---|
| **SBX-6 versions vs Jira History tab** | `uv run codeatlas collect --change .recordings/try/change.json --policy .recordings/try/policy.yaml --store .recordings/try/store` then open `.recordings/try/store/<hash>/bundle.json` and look at `versions` | 4 versions; version 1 is the description before history 10093, versions 2–4 match the "after" text of each entry in SBX-6 → History |
| Same thing offline | add `--fixtures tests/fixtures/jira` | the same 4 versions from the recording |
| **SBX-2 stale-approval FAIL** | change the change file to `SBX-2`, run `collect` | once SBX-2 is approved and then edited: verdict FAIL, C3 "stale approval", two evidence ids (approval and edit). See section 5: not possible yet |
| **Tamper refusal, exit 5** | `H=$(ls .recordings/try/store | head -1)`; flip one character in `.recordings/try/store/$H/bundle.json` with an editor; `uv run codeatlas replay .recordings/try/store/$H; echo $?` | `error (ReplayMismatchError): bundle hash does not match…`, exit code **5** |
| Replay of an untouched bundle | `uv run codeatlas replay tests/fixtures/bundles/cb973272d317b0a5e4d2f5b2198bf53740bac9ab2c93d07d581a51ff40ea4f66` | `match yes`, exit code 0 |
| **Wrong token, exit 3** | `CODEATLAS_JIRA_TOKEN=wrong uv run codeatlas collect --change .recordings/try/change.json; echo $?` | `error (CollectionError): jira request failed with HTTP 401…`, exit code **3**, no new bundle |
| Missing settings, exit 4 | run `collect` from a folder without `.env` and without `--fixtures` | `missing setting(s) … CODEATLAS_JIRA_BASE_URL…`, exit code 4 |
| No Jira key, UNKNOWN | a change file with `"work_items": []` | verdict UNKNOWN, every case "no work item", exit code 2 |
| Show a stored verdict | `uv run codeatlas show <bundle folder>` (or `--json`) | the verdict and all five case results |

## 3. Rerun the evaluation and read the report

```bash
# 1. once the Approved transition exists in SBX (section 5): create generated issues
uv run python -m tools.scenario_generator --count 50 --seed 2026
#    -> docs/report/data/ground-truth-gen-2026-50.json
# 2. mutation score (about 40 minutes)
uv run python scripts/mutation_score.py --out docs/report/data/mutation-score.json \
  --target src/codeatlas/analyze/lifecycle.py --target src/codeatlas/evaluate/rules/base.py \
  --target src/codeatlas/evaluate/rules/c1_changed_after_impl.py \
  --target src/codeatlas/evaluate/rules/c3_not_approved.py \
  --target src/codeatlas/evaluate/rules/c6_superseded_version.py \
  --target src/codeatlas/evaluate/rules/c7_test_evidence.py \
  --target src/codeatlas/evaluate/rules/c8_priority_authority.py \
  --tests tests/unit/test_lifecycle.py tests/integration/test_lifecycle_recordings.py \
          tests/unit/test_rules.py tests/property/test_lifecycle_properties.py \
          tests/property/test_rules_properties.py
# 3. the report
uv run python scripts/evaluate_phase1.py --truth docs/report/data/ground-truth-gen-2026-50.json
```

Reading `docs/report/phase1-evaluation.md`:

- **Section 1** shows what CodeAtlas says about SBX-1..7 as recorded.
- **Section 2** compares 50 generated issues with the oracle:
  - **version exact match** (every version's text equal to what the generator wrote);
  - for each case: correct, **false PASS** (oracle VIOLATED, CodeAtlas SATISFIED: the dangerous
    error), **false FAIL** (the reverse), and other (UNKNOWN where a judgement was expected).
- **Section 3** is determinism (same `verdict_hash` from two processes) and the tamper test.
- **Section 4** is branch coverage, the mutation score with every surviving mutant, and the
  number of property-test examples.

## 4. Break it on purpose

Make one change, run the named test, see it fail, then `git checkout -- <file>`.

| Break | File and change | Test that must fail |
|---|---|---|
| 1. Stale approvals pass | `src/codeatlas/evaluate/rules/c3_not_approved.py`: `edited_at > approved_at` → `edited_at < approved_at` | `uv run pytest tests/unit/test_rules.py -k stale` |
| 2. Tampering accepted | `src/codeatlas/bundle/store.py`: `if sha256_hex(raw) != folder.name:` → `if False:` | `uv run pytest tests/unit/test_bundle.py -k ac5` |
| 3. Lists no longer sorted | `src/codeatlas/bundle/canonical.py`: `sorted(value, key=_sort_key)` → `value` | `uv run pytest tests/property/test_bundle_properties.py` |
| 4. Broken history trusted | `src/codeatlas/analyze/lifecycle.py`, in `_chain_check`: `== comparison_form(after)` → `!= comparison_form(after)` | `uv run pytest tests/unit/test_lifecycle.py tests/property/test_lifecycle_properties.py` |
| 5. 429 not retried | `src/codeatlas/collect/jira/client.py`: `if status == 429 or status >= 500:` → `if status >= 500:` | `uv run pytest tests/unit/test_jira_client.py -k 429` |

## 5. Known limitations and what could not be verified

**Limitations of the build**

- **No GitHub or git evidence yet.** The collectors are Yusra's (step 9). The PR's commit times,
  which C1 and C6 need, come from a change file (CLI) or from the ground truth's commit markers
  (evaluation). Reviews, governance, code and semantic stages are empty in every bundle, with a
  collection note saying so.
- **Approver groups do not exist** in your Jira (`GET /group/member` answers 404), so the policy
  lists approvers and priority authority by account id. A policy that names only a group makes
  C3 UNKNOWN ("approvers unknown").
- **The generator uses one Jira account.** Every generated approval is by an approver, so
  "approved by a non-approver" is tested only in unit tests. C8 is tested both ways by
  evaluating each issue under two policies (that account with and without authority).
- **C1 and C6 both fire** for an edit after the last commit (reconciliation F14, left open in the
  spec). Both are REVIEW, so the verdict is the same.
- **Wiki markup:** only headings, `#` lists and `*` bullets are converted. Other markup (e.g.
  `*bold*`) in a description's history may not match the ADF text, which marks that field
  UNRESOLVED and makes C1/C3/C6 UNKNOWN: a safe failure, never a guess.
- **mutmut was not used.** It refuses native Windows (its issue #397) and this machine has no WSL
  distribution or Docker; `scripts/mutation_score.py` applies the same kind of mutations.
- **Two OS images** (§10 step 3) are covered by a golden hash (Windows) checked on Linux CI, not by
  a CI matrix; that job is Saleha's (step 15).
- **Live bundles hold real account ids** (only fixtures are sanitised). Keep them in the
  git-ignored `bundles/` or `.recordings/`.

**State of the demo issues (recorded 2026-10-07)**

SBX-1..5 and SBX-7 have **no history yet**: they are in To Do, never approved, with no priority
changes or edits. So today every one of them is C3 "not approved" (FAIL), which is correct for
their current state, not for their intended scenario. Missing per issue:

| Issue | Intended | Still to do in Jira |
|---|---|---|
| SBX-1 | C3 PASS | move to Approved |
| SBX-2 | C3 FAIL stale | move to Approved, then edit the description |
| SBX-3 | C3 FAIL never approved | nothing (already correct) |
| SBX-4 | C8 REVIEW | approve, then raise priority as someone without authority (with one account: evaluate with a policy that leaves you out of `priority_authority`) |
| SBX-5 | C8 PASS | approve, then raise priority as an authority |
| SBX-6 | 4 versions | done (verified: 4 versions, chain resolved) |
| SBX-7 | C1 REVIEW | approve, then edit after the PR's first commit time in your change file |

After doing them: `uv run python scripts/record_jira.py SBX-1 SBX-2 SBX-3 SBX-4 SBX-5 SBX-6 SBX-7`,
then rerun the evaluation.

**Not verified**

- Behaviour under a real Jira 429: retries are tested only against mocked responses (Atlassian's
  documented `Retry-After`). The generator does not log a 429 it waited out, so whether the
  50-issue run was ever rate limited is unknown.
- The exact wording of Jira's workflow editor options (used only in chat, not in code).
- Group membership reading (`GET /group/member`) with an existing group: the sandbox has none, so
  only the 404 path was observed live.

## 6. Pull requests in merge order

All PRs target `develop` and each contains the ones before it, so merge them **in this order with
"Create a merge commit"**. Squash-merging would rewrite the commits the next PR is built on, and the
agent may not force-push a rebase. After each merge the next PR's diff shrinks to its own commits.

| Order | PR | Step | Contains |
|---|---|---|---|
| 1 | #5 | setup | `CLAUDE.md` with the task authority, the mid-eval spec, step 1 spec, test dependencies, network blocked in tests |
| 2 | #6 | 2a | schema core types and evidence models (needs Yusra's approval for `Adr`) |
| 3 | #7 | 2b | policy (account-id approvers), bundle, verdict, collector protocols |
| 4 | #9 | 3 | canonical form, hashes, store, replay, `errors.py`, G1 sample bundle (replaces closed #8) |
| 5 | #10 | 4 | rule protocol, registry, truth table, `evaluate_bundle`, **G1 checkpoint** (tag `g1` after this) |
| 6 | #11 | 5a | ADF and wiki markup to plain text, step 5 spec |
| 7 | #12 | 5b | read-only Jira client with paging and retries |
| 8 | #13 | 5c | recorder, sanitiser, fixture source, SBX-1..7 recordings |
| 9 | #14 | 6a | project status categories (+ step 6 spec) |
| 10 | #15 | 6b | lifecycle reconstruction |
| 11 | #16 | 7 | rules C1, C3, C6, C8, C7 stub, mutation-score script |
| 12 | #17 | 8 | pipeline, CLI, config, logging |
| 13 | #18 | eval | scenario generator, evaluation script, this guide and the report |
