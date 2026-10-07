# Step 7: Rules C1, C3, C6, C8 and the C7 stub

- **Owner:** Areej (@areej8). Reviewer for expected results: Yusra (spec §13: "reviewed by someone
  other than the rule author").
- **Step:** 7 of the mid-evaluation spec (section 10)
- **Prerequisites:** step 4 (rule framework), step 6 (lifecycle facts)
- **Spec sections:** 3 (cases), 7.1, 7.5 (`cases` policy), 13 (four fixture bundles per rule),
  17 (AC08, AC09, AC12)
- **Branch:** `feature/rules`, stacked on `feature/lifecycle`

## Purpose

Judge a frozen bundle against four contradiction cases about the requirement's lifecycle, with
results that cite the exact evidence, and never claim PASS when the evidence is missing or the
history is broken.

## Shared rules of judgement (`evaluate/rules/base.py` helpers)

1. **Keys.** The requirements judged are the distinct `work_items` keys.
   - No work item: every case below returns INSUFFICIENT_EVIDENCE, `required=True`, message
     "no work item" (spec AC12: verdict UNKNOWN, no case returns PASS).
   - A key with no `LifecycleFacts` in the bundle (issue not found) is INSUFFICIENT_EVIDENCE for
     that key.
2. **Broken history.** Any version of a key with `content_resolved=False` means a change happened
   that the changelog does not show, at an unknown time. C1, C3 and C6 then cannot be SATISFIED for
   that key: the result is INSUFFICIENT_EVIDENCE (unless another fact already gives VIOLATED).
3. **Combining keys.** VIOLATED if any key is violated, otherwise INSUFFICIENT_EVIDENCE if any key
   is, otherwise SATISFIED. `evidence_ids` are the union, sorted.
4. **Severity.** Defaults: C3 `block`; C1, C6 and C8 `review`; C7 `review`. Overridden by
   `policy.cases[<case>].severity` when set.
5. **Requirement edits** are the `change_events` whose `field` is in
   `policy.requirement_fields`.
6. **Implementation times** come from `bundle.commits`: implementation began at the earliest
   `committed_at`, and the PR's last commit is the latest. With no commits, C1 and C6 are
   INSUFFICIENT_EVIDENCE.
7. **Approvers unknown.** When the pipeline cannot resolve the approvers (group unreadable and no
   account ids), it adds the collection note `approvers unknown: <KEY>`. C3 is then
   INSUFFICIENT_EVIDENCE for that key instead of "not approved".

## The cases

| Case | VIOLATED when | Cites |
|---|---|---|
| **C1** requirement changed after implementation began | a requirement edit happened after the earliest commit | the edit events, the earliest commit |
| **C3** requirement not approved, or approval stale | no valid approval (`latest_valid_approval_at` is None) → "not approved"; or `last_requirement_edit_at > latest_valid_approval_at` → "stale approval" | the approval and the later edit (spec AC08: "both evidence ids") |
| **C6** code linked to a superseded version | a requirement edit happened after the PR's last commit | the edit events, the last commit |
| **C8** priority changed after approval without authority | a `PriorityChange` with `after_first_approval` and `actor_has_authority is False` | the priority change events |
| **C7** (stub) changed behaviour has no test evidence | never: full rule is Yusra's (step 13) | — |

- **C8 details.**
  - An automation change (no actor) is ignored.
  - A change after approval by a person whose authority is unknown (`actor_has_authority=None`,
    actor present) is INSUFFICIENT_EVIDENCE.
  - With no work item, C8 is INSUFFICIENT_EVIDENCE.
- **C7 stub.**
  - If any key carries the label `policy.cases["C7"].required_when_label`: INSUFFICIENT_EVIDENCE,
    `required=True` ("test evidence is not collected yet").
  - Otherwise: NOT_APPLICABLE, `required=False`.
- **C1 and C6 overlap.** An edit after the last commit is also after the first commit, so C1 and
  C6 both fire. Both are severity `review`, so the verdict is REVIEW either way. Recorded as an
  open point of the mid-evaluation spec (reconciliation F14), not changed here.

## Fixture bundles and expected results (written before the rules)

Bundles are schema objects built in code in `tests/unit/test_rules.py` (spec §13 allows schema
objects in unit tests). Times are minutes after 2026-10-01T05:00Z.

| Rule | Bundle | Facts | Expected outcome |
|---|---|---|---|
| C1 | pass | edits at 5; commits at 10, 20 | SATISFIED |
| C1 | review | edits at 5, 15; commits at 10, 20 | VIOLATED (review), cites edit at 15 and commit at 10 |
| C1 | unknown | edits at 5; no commits | INSUFFICIENT_EVIDENCE |
| C1 | edge | edit exactly at the first commit's time (10) | SATISFIED (not *after*) |
| C3 | pass | approved by approver at 5, last edit at 3 | SATISFIED |
| C3 | fail | approved at 5, edit at 9 | VIOLATED (block), "stale approval", cites approval and edit |
| C3 | fail | never approved | VIOLATED (block), "not approved" |
| C3 | unknown | no work item | INSUFFICIENT_EVIDENCE, "no work item" |
| C3 | edge | approved at 5, revoked at 7, re-approved at 9, last edit at 8 | SATISFIED (latest valid approval is after the edit) |
| C3 | edge | version content unresolved, approved after the last logged edit | INSUFFICIENT_EVIDENCE |
| C3 | edge | approvers unknown note for the key | INSUFFICIENT_EVIDENCE |
| C6 | pass | edits at 5; commits at 10, 20 | SATISFIED |
| C6 | review | edit at 25; commits at 10, 20 | VIOLATED (review), cites edit and commit at 20 |
| C6 | unknown | no commits | INSUFFICIENT_EVIDENCE |
| C6 | edge | edit at 15 (between commits) | SATISFIED for C6 (C1 fires instead) |
| C8 | pass | priority raised after approval by authority | SATISFIED |
| C8 | review | raised after approval by non-authority | VIOLATED (review), cites the priority event |
| C8 | unknown | raised after approval, authority unknown | INSUFFICIENT_EVIDENCE |
| C8 | edge | raised before approval by non-authority; and an automation change after approval | SATISFIED |
| C7 | n/a | no label | NOT_APPLICABLE, not required |
| C7 | unknown | label `requires-test-evidence` | INSUFFICIENT_EVIDENCE, required |
| all | several keys | C3: one key fine, one stale | VIOLATED, evidence from the stale key only |
| all | policy | `cases.C1.severity = block` | C1 violation has severity `block` |

Verdict-level expectations for the owner's SBX scenarios, once the issues are in their final
state (for the evaluation guide):

| Issue | Expected |
|---|---|
| SBX-1 approved only | C3 SATISFIED |
| SBX-2 approved, then edited | C3 VIOLATED stale → FAIL |
| SBX-3 never approved | C3 VIOLATED not approved → FAIL |
| SBX-4 priority raised by non-authority | C8 VIOLATED → REVIEW (if otherwise approved) |
| SBX-5 priority raised by authority | C8 SATISFIED |
| SBX-7 edited after PR commits | C1 VIOLATED → REVIEW (C3 also fails unless re-approved) |

## Acceptance criteria

- **AC1 (spec AC09, Phase 1 cases)** Every row of the fixture table gives its expected outcome.
- **AC2 (spec AC08)** Stale approval: C3 FAIL with message "stale approval" citing both the
  approval's and the edit's evidence ids.
- **AC3 (spec AC12)** No work item: every Phase 1 case is INSUFFICIENT_EVIDENCE with "no work
  item", the verdict is UNKNOWN, and no result is SATISFIED.
- **AC4 (property)** A bundle with any broken changelog chain never yields PASS.
- **AC5** Rules are pure: the same bundle gives the same results, whatever the order of its lists.
- **AC6** `default_rules()` returns C1, C3, C6, C7 and C8; the G1 sample verdict is regenerated with
  them and still replays.

## Files to touch

- `src/codeatlas/evaluate/rules/{base,__init__}.py`, `c1_changed_after_impl.py`,
  `c3_not_approved.py`, `c6_superseded_version.py`, `c7_test_evidence.py`,
  `c8_priority_authority.py`
- `tests/unit/test_rules.py`, `tests/property/test_rules_properties.py`
- `tests/fixtures/bundles/<sample>/verdict.json` (regenerated), `tests/unit/test_verdict.py`

## Out of scope

- C2, C4, C5, C9 and the full C7 (Yusra); trace links (step 13).
