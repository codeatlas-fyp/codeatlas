# Step 6: Lifecycle reconstruction

- **Owner:** Areej (@areej8)
- **Step:** 6 of the mid-evaluation spec (section 10)
- **Prerequisites:** step 5 (Jira collector, ADF/wiki text)
- **Spec sections:** 3 (M1, M2), 5 (stage 5), 7.1 (`ChangeEvent`, `RequirementVersion`,
  `Approval`, `PriorityChange`, `LifecycleFacts`), 17 (AC06, AC07, AC08)
- **Branch:** `feature/lifecycle`, stacked on `feature/jira-recorder`

## Purpose

Rebuild every version of a requirement from Jira's changelog, check that the history really
chains together, and record who requested, approved, prioritised and was assigned each version.
Never guess text: where the history does not chain, the text is UNRESOLVED and the events stay.

## Inputs and outputs

`analyze.lifecycle.reconstruct(source: JiraHistory, context: LifecycleContext, text: TextForms) -> Lifecycle`

| Type | Fields |
|---|---|
| `JiraHistory` (input) | `issue: dict` (raw), `changelog: list[dict]` (raw, any order) |
| `LifecycleContext` (input) | `policy: Policy`, `approvers: frozenset[str]`, `authorities: frozenset[str] \| None` (None = unknown), `new_statuses: frozenset[str]`, `retrieved_at`, `extractor_version` |
| `TextForms` (injected) | `current(value) -> str` (ADF), `history(value) -> str` (wiki) |
| `Lifecycle` (output) | `events: list[ChangeEvent]`, `versions: list[RequirementVersion]`, `facts: LifecycleFacts` |

The containers hold only raw JSON or schema types; they are not parallel domain models.
`TextForms` is injected because `analyze` may not import `collect` (where the ADF converter
lives, spec §8); the pipeline passes `adf_to_text` and `wiki_to_text`.

New collector call (needed for "new"-category statuses):
`JiraClient.fetch_status_categories(project_key) -> dict[str, str]` (status name → category key),
from `GET /rest/api/3/project/{key}/statuses`, whose shape (issue types → statuses →
`statusCategory.key` in `new`/`indeterminate`/`done`) is visible in the SBX recording of
2026-10-07. The recorder writes `<PROJECT>.statuses.json`.

## Rules

1. **Change events.** Every changelog item becomes one `ChangeEvent`, all fields included.
   - `evidence_id = jira:<key>:history:<history id>:<field id>`
   - `source_ref = <site>/browse/<key>`
   - `at` is UTC, truncated to the second.
   - Values:
     - rich-text fields (current value is ADF): text from `TextForms.history`;
     - `assignee` and `reporter`: account ids (`from`/`to`);
     - everything else: `fromString`/`toString`.
2. **Requirement fields** are `policy.requirement_fields` taken as Jira field ids. A field the
   issue does not have (e.g. `acceptance_criteria` in SBX, where the criteria are inside the
   description) is `None` in every version.
3. **Versions.**
   - Version 1 starts at the issue's `created` time.
   - Each changelog history that changes at least one requirement field starts the next version;
     several fields changed in one history make one version.
   - `valid_to` is the next version's `valid_from`.
   - `status`, `priority` and `assignee_account_id` are the values in force at `valid_from`.
4. **Text.**
   - The last version holds the current values (ADF → `TextForms.current`).
   - Earlier versions hold the `from` text of the following change.
5. **Chain check.** For each requirement field, in comparison form (lines trimmed, runs of spaces
   collapsed, blank lines and leading list markers removed):
   - the newest change's `to` must equal the current value;
   - each older change's `to` must equal the next change's `from`.
   A failure marks that field UNRESOLVED (`None` text, field in `unresolved_fields`, reason
   added) in every version before the next verified change, or in every version when the newest
   change does not match the current value. Events are never dropped (AC07).
6. **Approvals.**
   - **Approval:** each transition into `policy.approval.status`.
   - `by_approver_group`: the actor is in `approvers`.
   - `revoked_at` (owner decision): time of the first later transition from that status into a
     status whose category is `new`.
   - `latest_valid_approval_at`: the latest approval with `by_approver_group` true and no
     `revoked_at`.
7. **Priority changes.**
   - `after_first_approval`: the change is later than the first approval by an approver.
   - `actor_has_authority`:
     - `None` when the actor is Jira itself (no author) or authority is unknown (`authorities`
       is None);
     - otherwise, whether the actor is in `authorities`.
8. **Other facts.**
   - `requester_account_id`: the reporter (owner decision; confirmed on the first recording,
     where `reporter` and `creator` are the same person).
   - `last_requirement_edit_at`: time of the last requirement-field change.
   - `assignee_history`: `(created, first assignee)` then every assignee change.
   - `labels`: current labels.

## Acceptance criteria

- **AC1 (AC06 of the spec)** Given the recorded SBX-6 (3 description edits), then 4 versions
  exist, all resolved, version 4 equals the current description, and each version's text equals
  the history's `from`/`to` texts.
- **AC2 (property)** For any sequence of edits to any requirement fields, replaying the change
  events forward from version 1 reproduces every later version and the current value.
- **AC3 (AC07)** Given a changelog whose `from` does not match the previous `to`, then the versions
  before the break have that field UNRESOLVED, later versions are resolved, and every change
  event is still present.
- **AC4 (property)** Corrupting one `from` text in any generated history never leaves all versions
  resolved, and never removes an event.
- **AC5** A history changing two requirement fields at once makes one version.
- **AC6** An issue with no requirement-field changes has one version holding the current values.
- **AC7** Approval: a transition into `Approved` by an approver is valid; by a non-approver it has
  `by_approver_group=False`; a move from `Approved` to a `new`-category status sets `revoked_at`;
  a move to `In Progress` or `Done` does not; a later re-approval becomes the latest valid one.
- **AC8 (data for spec AC08)** Approved by an approver, then the description edited: then
  `last_requirement_edit_at > latest_valid_approval_at`, and both evidence ids are available.
- **AC9** Priority: changes before and after the first approval are flagged correctly; actor in or
  out of `authorities`; author missing → `None`; `authorities=None` → `None`.
- **AC10** `status`, `priority` and `assignee_account_id` of each version are the values in force at
  `valid_from`; `assignee_history` and `labels` are filled; times are UTC seconds.
- **AC11** Events are sorted by time then history id, whatever the changelog order.

## Fixtures

| Fixture | Expected |
|---|---|
| `tests/fixtures/jira/SBX-6.*` (recorded) | 4 versions, all resolved; 3 description events |
| `tests/fixtures/jira/SBX-1..5,7.*` (recorded) | 1 version each, no events (no history yet on 2026-10-07) |
| `tests/fixtures/jira/SBX.statuses.json` (recorded) | `To Do`→`new`, `In Progress`→`indeterminate`, `Approved`→`done`, `Done`→`done` |

Synthetic histories for AC2–AC9 are built in code inside the tests (Hypothesis and small
builders); they are inputs to a pure function, not committed fixture files.

## Files to touch

- `src/codeatlas/analyze/lifecycle.py`
- `src/codeatlas/collect/jira/client.py`, `recorder.py` (status categories); `scripts/record_jira.py`
- `tests/unit/test_lifecycle.py`, `tests/property/test_lifecycle_properties.py`,
  `tests/integration/test_lifecycle_recordings.py`

## Out of scope

- Rules over these facts (step 7), criteria splitting (step 13, Yusra).
