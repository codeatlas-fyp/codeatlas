# Step 2: Schema v0

- **Status:** approved 2026-10-07 with changes (see "Decisions" at the end). PR 2a implemented on
  `feature/schema-core`; PR 2b (policy, bundle, protocols) next.
- **Owner:** Areej (@areej8). `schema/` is co-owned by all three (CODEOWNERS), so Yusra or Saleha
  reviews.
- **Step:** 2 of the mid-evaluation spec (section 10)
- **Prerequisites:** step 1 (PR 1a merged)
- **Blocks:** steps 3, 4, 7, 9 and 13, and gate G1
- **Spec sections:** 7.1, 7.2, 7.5, 9 (G1), 15 (models frozen, `extra="forbid"`, UTC)

## Purpose

Define every domain model that crosses a module boundary, so that collectors, analysers, the
bundle, the rules and the API all speak the same validated types. After G1 these models are
frozen as `schema-v0`. A change then needs an issue, a `schema_version` note and approval from
all three members.

## Inputs and outputs

There is no runtime behaviour. The output is the package `codeatlas.schema`, which exports:

- **Type aliases:** `CaseId`, `Outcome`, `LinkState`, `VerdictValue` (§7.1), and `UtcDatetime`
  (rule R3 below).
- **Section 7.1 models:**
  - `Evidence`, `ChangeRef`, `WorkItemRef`, `ChangeEvent`
  - `RequirementVersion`, `Approval`, `PriorityChange`, `LifecycleFacts`
  - `Commit`, `Review`, `ResolvedPerson`
  - `OwnerRule`, `GovernanceSnapshot`, `GovernanceChange`
  - `CodeEntity`, `ChangedEntity`, `CodeEdge`
  - `Criterion`, `SemanticCandidate`, `TraceLink`
  - `CheckResult`, `Verdict`, `EvidenceBundle`
- **Models section 7 uses but does not define** (proposed in S1 and S2): `Adr`, `Policy` and
  its parts, `IdentityMap` and `IdentityEntry`.
- **Section 7.2 protocols:** `WorkItemSource`, `ChangeSource` (see S6 for `Rule`).

Field names, types and `Literal` values are copied exactly from section 7.1. Anything not in
section 7.1 is listed under "Proposed additions" and needs the owner's approval before coding.

## Model rules

- **R1** Every model uses `model_config = ConfigDict(frozen=True, extra="forbid")` (§15).
- **R2** `Evidence` subclasses inherit `evidence_id`, `source`, `source_ref`, `retrieved_at` and
  `extractor_version`.
- **R3** Every `datetime` field is typed `UtcDatetime`: a naive datetime is rejected, and an
  aware one is converted to UTC. This includes the `datetime` inside `assignee_history`
  tuples. (§7 "UTC-aware datetimes"; §15 `DTZ`.)
- **R4** `EvidenceBundle.schema_version` is `Literal["0"]`.
- **R5** Field types are not narrowed or widened beyond section 7.1. Example: `fused_score` stays
  `float`; rounding is the bundle's job in step 3.
- **R6** No I/O, no business logic and no imports from other `codeatlas` packages (contract K4).

## Proposed additions and decisions (owner must approve or change)

These come from the reconciliation report's F3, F4, F8 and F9. Each one is the smallest change
that makes the section 7 contracts usable. None of them is coded until approved.

- **S1 — `Policy` and `IdentityMap`.** These are typed exactly as the section 7.5 YAML, with no
  extra keys. They live in `schema` because the `Rule` protocol takes a `Policy`, and §15 copies
  both into the bundle. `config.py` (a later step) loads YAML into them.
  - `Policy`:
    - `version: int`
    - `workitem_key_pattern: str`
    - `requirement_fields: list[str]`
    - `governance_paths: list[str]`
    - `approval: ApprovalPolicy(status: str, approver_group: str)`
    - `priority_authority_group: str`
    - `cases: dict[CaseId, CasePolicy]`, where
      `CasePolicy(severity: Literal["block","review"] | None = None, required_when_label: str | None = None)`
    - `semantic: SemanticPolicy(top_k: int)`
  - `IdentityMap`: `people: list[IdentityEntry]`, where
    `IdentityEntry(jira_account_id: str, github_login: str | None, git_emails: list[str])`.
- **S2 — `Adr`.** §7.1 references `"Adr"` but never defines it. It is Yusra's area (governance,
  step 10). The fields §7.3 needs: `adr_id: str`, `path: str`, `status: str | None`,
  `scope: list[str]` (path globs), `requirement_keys: list[str]`. Options: (a) define these now
  with Yusra's agreement, so G1 can freeze; (b) leave `adrs` out of v0 and add it after G1 with
  three approvals. **Proposed: (a), confirmed by Yusra in review.**
- **S3 — Missing `EvidenceBundle` fields** (§15, §16, AC14):
  - `collected_at: UtcDatetime`: the time rules use instead of the clock (§15).
  - `run_id: str` (§15 logging).
  - `policy: Policy` and `identity_map: IdentityMap`: copied in so replay never reads live files
    (§15).
  - `model_file_hashes: dict[str, str]`: `model_id → sha256` (§16, AC14).
  - **No `bundle_hash` field inside the bundle.** The bundle is content-addressed: its hash is
    its store name, and `Verdict.bundle_hash` carries it. That makes §7.4's "bundle without
    bundle_hash" hold by construction.
- **S4 — Labels for C7.** `required_when_label: requires-test-evidence` is a Jira label in the
  sandbox guide (SBX-5), and no model holds labels. Proposed: `labels: list[str]` on
  `LifecycleFacts`, holding the labels at collection time.
- **S5 — Version contents (F8).**
  - Add `unresolved_fields: list[Literal["summary","description","acceptance_criteria"]]` to
    `RequirementVersion`. An unresolved field's text is `None`. `content_resolved` stays and
    equals `not unresolved_fields`, which a model validator enforces. This keeps the CA-4
    per-field behaviour and still meets AC07.
  - **Documented, no schema change:**
    - A new version starts only when a requirement field changes (AC06).
    - `status`, `priority` and `assignee_account_id` hold their values at `valid_from`.
    - `change_events` holds every changelog item, for every field, so status, priority and
      assignee changes can be cited.
- **S6 — Evidence ids for lifecycle facts (F9).**
  - Add `evidence_id: str` to `Approval` (the status-change `ChangeEvent` id) and to
    `PriorityChange` (the priority `ChangeEvent` id), so C3 and C8 can cite them (AC08).
  - The `ChangeEvent` id format follows the §7.1 example: `jira:<key>:history:<history_id>:<field>`.
- **S7 — Meaning of `Approval.revoked_at` and `requester_account_id`** (no schema change).
  - `revoked_at`: proposed for v0 that it stays `None` (no revocation). Staleness is
    `last_requirement_edit_at > latest_valid_approval_at`, and a later approval supersedes an
    earlier one.
  - `requester_account_id`: the Jira `reporter`. I have not yet verified the difference between
    Jira's `reporter` and `creator` against a recorded response; that is part of the R2 spike.
- **S8 — Where the protocols live.**
  - `WorkItemSource` and `ChangeSource` go in `schema/protocols.py` in this step (G1 requires
    them).
  - `Rule` goes in step 4 (`evaluate/rules/base.py`, §8). It also needs §15's `required_evidence`
    and `version`, which §7.2 omits.
  - Raw types named in §5 and §6 (`RequirementTimeline`, `JiraIssueRaw`, `JiraChangelogRaw`,
    `GroupMembers`, `PullRequestRaw`, `DiffHunk`) are **not** created. §7.2 says collectors
    return raw JSON.
  - `EvaluateRequest` and `EvaluationResult` are defined in step 8.

**Not decided here:**
- Canonical list ordering and float-as-string encoding (F7) belong to step 3.
- Where the G1 sample bundle comes from (F12) also belongs to step 3.

## Files to touch

| File | Content |
|---|---|
| `src/codeatlas/schema/__init__.py` | Re-exports the public names |
| `src/codeatlas/schema/types.py` | `CaseId`, `Outcome`, `LinkState`, `VerdictValue`, `UtcDatetime`, base `Model` (R1) |
| `src/codeatlas/schema/evidence.py` | §7.1 models, plus S2, S5 and S6 |
| `src/codeatlas/schema/policy.py` | `Policy`, `IdentityMap` and parts (S1) |
| `src/codeatlas/schema/bundle.py` | `EvidenceBundle` (with S3), `CheckResult`, `Verdict` |
| `src/codeatlas/schema/protocols.py` | `WorkItemSource`, `ChangeSource` (S8) |
| `tests/unit/test_schema.py` | Tests below |

**PR size:** about 250 lines of models and 250 of tests, which is over 400. The split:
- **PR 2a:** `types.py`, `evidence.py` and their tests.
- **PR 2b:** `policy.py`, `bundle.py`, `protocols.py` and their tests.

## Acceptance criteria

- **AC1 (§10)** Given one valid instance of every model, when it is dumped with
  `model_dump_json()` and loaded with `model_validate_json()`, then the result equals the
  original. A guard test fails if a model exported from `codeatlas.schema` has no sample, so
  every model is covered.
- **AC2 (§10)** Given the sample of any model as a dict plus one unknown key, when it is
  validated, then `ValidationError` is raised.
- **AC3** Given any model instance, when a field is assigned, then `ValidationError` is raised
  (frozen).
- **AC4** Given a naive datetime in any `UtcDatetime` field, validation fails. Given
  `2026-10-03T09:00:00+05:00`, the stored value is `2026-10-03T04:00:00+00:00`.
- **AC5** Given `schema_version="1"`, `case="C10"`, `outcome="satisfied"` or
  `state="LINKED"`, validation fails.
- **AC6** Given a full `EvidenceBundle` containing at least one of every nested model, two
  `model_dump_json()` calls give identical bytes, and the round trip equals the original.
- **AC7 (if S5 is approved)** A `RequirementVersion` whose `content_resolved` disagrees with
  `unresolved_fields` is rejected.
- **AC8** `uv run lint-imports` still reports K4 (schema imports nothing from `codeatlas`) as
  kept.

## Test data

- All samples are schema objects built in code, in `tests/unit/test_schema.py`. This is allowed
  for unit tests (§13), so no fixture files are needed.
- Sample values use fake identities (`user-01`, `user-01@example.test`) and the SBX key format.
- No real account ids or emails are used.

## Out of scope

- Canonical JSON, hashing and the sample bundle (step 3).
- The `Rule` protocol and the verdict (step 4).
- `config.py` YAML loading, `errors.py` and `logging.py`. These are Phase 1 work that no step
  claims (F3); proposed home: step 3 for `errors.py`, step 8 for `config.py` and `logging.py`.
- JSON Schema export (§6) as a file. `model_json_schema()` keeps working; a docs export can come
  with step 16.

## Decisions (owner, 2026-10-07)

- **S1 approved with a change.** Group membership may not be readable on our Jira plan, so the
  approver and priority-authority lists also accept explicit account ids:
  - `ApprovalPolicy(status: str, approver_group: str | None = None, approver_account_ids: list[str] = [])`
  - `AuthorityPolicy(group: str | None = None, account_ids: list[str] = [])`
  - Validator on both: a group **or** at least one account id is required.
  - `Policy.priority_authority: AuthorityPolicy` replaces `priority_authority_group: str`.
  - The sandbox approval status is `Approved`.
- **S2** option (a). Yusra must approve the PR that contains `Adr` (PR 2a).
- **S3** approved as written: content-addressed bundle, no `bundle_hash` inside.
- **S4** approved: `labels` on `LifecycleFacts`. The C7 label goes on a new SBX issue later.
- **S5** approved. **S6** approved. **S8** approved (`Rule` protocol in step 4).
- **S7** keep both fields as in §7.1. The meaning of `revoked_at` is decided in step 6.
  Proposal for step 6: an approval is revoked when the issue moves from `Approved` back to a
  "To Do"-category status. Requester = Jira `reporter`, verified against the first recording.
- **PR split** 2a / 2b approved. `errors.py` in step 3; `config.py` and `logging.py` in step 8.

### §7.5 policy YAML, as amended by S1

```yaml
# .codeatlas/policy.yaml
version: 1
workitem_key_pattern: "[A-Z]+-\d+"
requirement_fields: [summary, description, acceptance_criteria]
governance_paths: [CODEOWNERS, .github/CODEOWNERS, docs/CODEOWNERS, docs/adr/**, .codeatlas/**, .github/workflows/**, .pre-commit-config.yaml]
approval:
  status: Approved
  approver_group: sbx-requirement-approvers   # optional if approver_account_ids is given
  approver_account_ids: []                    # optional if approver_group is given
priority_authority:
  group: sbx-priority-authority               # optional if account_ids is given
  account_ids: []                             # optional if group is given
cases:
  C3: { severity: block }
  C5: { severity: block }
  C7: { required_when_label: requires-test-evidence }
semantic: { top_k: 5 }
```

### Real SBX issues (replace the sandbox guide's scenario list)

| Issue | Scenario | Expected |
|---|---|---|
| SBX-1 Borrow a book | approved only | C3 PASS |
| SBX-2 Return a book | approved, then edited | C3 FAIL (stale approval) |
| SBX-3 Search catalogue by title | never approved | C3 FAIL |
| SBX-4 Late fee | priority raised by non-authority | C8 REVIEW |
| SBX-5 Reserve a book | priority raised by authority | C8 PASS |
| SBX-6 Member registration | 3 description edits | lifecycle: 4 versions |
| SBX-7 Renew a loan | edited after PR commits | C1 REVIEW |

**Follow-up (not now):** rewrite `docs/setup/jira-sandbox.md` to match this table; add a new SBX
issue with the `requires-test-evidence` label for C7.

## PR 2a as implemented

- Files: `schema/types.py`, `schema/evidence.py`, `schema/__init__.py` (exports PR 2a names
  only), `tests/unit/test_schema.py`.
- Size: 651 changed lines (296 models and exports, 355 tests); over 400 because the tests build
  one sample of every model.
- AC6 (whole bundle) and the `CaseId`/`Outcome`/`VerdictValue` literal checks move to PR 2b with
  `bundle.py`.
