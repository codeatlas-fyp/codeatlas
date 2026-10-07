# CodeAtlas — Mid-Evaluation Implementation Specification

Oct 7, 2026 · @areej

This is the single authoritative specification for the CodeAtlas mid-evaluation build (15 October 2026). It overrides earlier plans where they disagree; section 2 lists every disagreement and how it was settled. Claude Code is instructed against this document, one phase at a time.

**How to use it.** Export it to Markdown and commit it as `docs/specs/mid-eval-spec.md`. Then give Claude Code: *"Implement Phase 1 according to docs/specs/mid-eval-spec.md, sections 5–10 and 13–15."* Each phase has its own acceptance criteria and a gate that must pass before the next phase starts.

**Decisions confirmed by the team (7 October).**

| Decision | Value |
| --- | --- |
| Mid-evaluation date | 15 October 2026 |
| Scope policy | Full core scope; time is managed with AI-assisted coding, not by cutting the core |
| Requirement source | Live Jira (sandbox project `SBX`) for the demo, plus recorded JSON fixtures for tests and as an offline fallback |
| Code and review source | Local git clone (pygit2) plus the GitHub REST API for pull requests and reviews |
| Team task tracking | Not Jira. GitHub Issues + a GitHub Projects board in the repository (section 11) |
| Development order | Sequential by architectural dependency: Areej → Yusra → Saleha, with interface gates (section 9) |
| Language | Python 3.12, single repository `codeatlas-fyp/codeatlas` |

**Status labels used throughout:** *Confirmed* (decided by the team or required by the FYP rubric), *Recommended* (an engineering decision made here, changeable), *Assumption* (proceeding without confirmation), *Open* (needs an answer; listed in section 20).

## 1. Project objective

CodeAtlas evaluates one pull request against the requirement it claims to implement and returns a verdict that can be reproduced exactly: **PASS, FAIL, REVIEW or UNKNOWN**, with every finding traced to the Jira change, commit, review or file that caused it.

**The problem.** A pull request links to a Jira ticket, but nothing checks whether the linked requirement was approved, whether it changed after coding began, who actually wrote the code compared with who was assigned, whether the responsible owner reviewed the final version, or whether the changed code is explained by the requirement at all. Existing AI reviewers (CodeRabbit, Qodo, Atlassian Rovo Dev) compare a PR with the ticket's *current* text using a language model, so their answer can change between runs and ignores the ticket's history.

**What CodeAtlas does differently (the technical contribution shown at mid-evaluation).**

1. **Time-aware requirement evidence.** Rebuilds every version of the requirement from the Jira changelog and records who requested, approved, prioritised, was assigned and implemented each version.
2. **Governance from the trusted base branch.** Ownership (CODEOWNERS), architecture decisions (ADRs) and policy are read from the base commit, so a PR cannot approve itself by editing its own rules.
3. **Typed trace links.** Every requirement-to-code link is labelled by how it is known: observed, deterministically derived, semantic candidate, conflicting or unresolved. Semantic similarity can suggest a link but never decides a verdict.
4. **Deterministic, replayable verdicts.** All evidence is frozen into a hashed bundle; `replay` recomputes the verdict offline and proves it is identical.

**Users.** Reviewers and tech leads deciding whether to merge; engineering managers checking that changes trace to approved requirements.

**Final system (beyond mid-evaluation).** A trained model that fuses evidence into trace links with a controlled error rate, requirement-change classification, an evaluation on labelled data, and wider integrations. These are explicitly not part of this build (section 3).

## 2. Document review: contradictions and corrections

The five input documents (Features & FRs, Mid-Eval Roadmap, Basic Action Plan, Scoped Action Plan, mid-eval rubric) and the repository disagree in fourteen places. Each row states the decision this spec takes.

| # | Conflict or problem | Source | Decision in this spec |
| --- | --- | --- | --- |
| D1 | Roadmap: mocked Jira JSON, one contradiction case, CLI only, deployment deferred | Roadmap §3, §4.3 | **Overridden.** Team chose live Jira + fixtures and full core scope; the coordinator's brief requires CI **and** CD with a live interface |
| D2 | Three different ownership splits (Roadmap: Yusra builds the spine; CONTRIBUTING: Areej Jira + rules; team: sequential Areej → Yusra → Saleha) | Roadmap §3.2, repo | **Sequential by dependency** (section 9). Roadmap split retired |
| D3 | FR-2.8 requires an indexed graph database; Roadmap says in-memory graph | FRs §4 | **No graph database.** One evaluation's graph has hundreds of nodes; an in-memory graph plus JSON bundles is enough and keeps replay simple |
| D4 | FR-1.4 entity identity across renames and moves | FRs §3 | **Deferred.** Hard and not needed for the demo; renamed entities are recorded as unresolved, never as failures |
| D5 | FR-4.4 approval states (Proposed / Approved / Rejected / Superseded) do not exist in Jira Software | FRs §6 | **Approval by declared convention** in config: a transition into a named status by a member of a named group |
| D6 | FR-6.5 checks that the owner is among reviewers; an approval of an older commit would pass | FRs §8 | **Stricter:** owner must approve the **head** commit; older approvals are stale |
| D7 | Repo `approval-convention.md` says a stale approval gives REVIEW; later decision says FAIL | repo vs spec | **FAIL** (requirement no longer approved). Doc to be corrected in Phase 1 |
| D8 | NFR-1 to NFR-6 promise precision ≥ 0.90, ≤ 2% false certainty, 99% equivalence | FRs §13 | **Not claimed at mid.** No labelled dataset exists yet. Mid reports measured results on the 8 scenarios only |
| D9 | FR-7.3–7.6 licences and vulnerabilities, FR-9 recommendation engine, FR-8.4 agent API, Jira plugin | FRs §9–11 | **Deferred or rejected** (section 3) |
| D10 | FR-10 incremental invalidation with 99% equivalence | FRs §12 | **Deferred.** Per-file parse cache only |
| D11 | FR-6.7 test evidence needs runtime coverage | FRs §8 | **SHOULD.** Coverage collection on the sandbox repo if time allows; otherwise an honest "insufficient evidence" result |
| D12 | Team tasks were set up in a Jira project `CA`; the team decided not to use Jira for its own work | conversation | **GitHub Issues + Projects** for tasks. Jira `SBX` stays: it is the product's input data, not team tracking |
| D13 | Repo has 5 commits, all by Areej; PRs merged into `main` | repo | Rubric caps member evidence when one member dominates. **Every member commits their own phase through PRs into `develop`** |
| D14 | A strictly sequential plan leaves two members idle for days, which also shows as uneven contribution | team request vs rubric | **Sequential gates, staggered starts**: later phases begin preparation work that does not depend on earlier code (section 9) |

## 3. Mid-evaluation scope

The build is one module, the **Evidence Case Engine**, plus the delivery surfaces the rubric requires. Everything in MUST is needed either to prove the contribution in section 1 or to meet a rubric item; nothing is included to look industrial.

**Contradiction cases.** The FR document's seven cases are kept with their numbers (C1–C7), and two more are added. Earlier drafts used R1–R8; the mapping is shown so old notes stay readable.

| Case | Detects | FR | Old id | Severity |
| --- | --- | --- | --- | --- |
| C1 | Requirement changed after implementation began | FR-6.1 | R1 | review |
| C2 | PR modifies its own governance (CODEOWNERS, ADR, policy, CI) | FR-6.2 | R5 | review |
| C3 | Requirement not approved, or approval is stale | FR-6.3 | R2 | block |
| C4 | Assignee differs from observed implementer | FR-6.4 | R7 | review |
| C5 | Required owner did not approve the head commit | FR-6.5 | R4 | block |
| C6 | Code linked to a superseded requirement version (requirement changed after the PR's last commit) | FR-6.6 | new | review |
| C7 | Changed behaviour has no test evidence | FR-6.7 | R6 | review |
| C8 | Priority changed after approval by someone without authority | new | R3 | review |
| C9 | Changed code not explained by the requirement | new | R8 | review |

**MUST HAVE**

| ID | Capability | Why it is MUST |
| --- | --- | --- |
| M1 | Jira adapter: issue, full changelog, group members; rich text → plain text; requirement version rebuild with chain check | Core contribution 1; team chose live Jira |
| M2 | Requirement lifecycle per version: requester, approver, priority and setter, assignee | Panel's first comment |
| M3 | Git + GitHub collection: merge-base, diff, commits, PR, reviews with reviewed commit | Needed by C1, C4, C5, C6 |
| M4 | Governance snapshot at the base commit: CODEOWNERS, ADR front matter, policy file; list of governance files the PR changes | Core contribution 2; C2, C5 |
| M5 | Code analysis: Tree-sitter entities, diff → changed entities, module import graph and direct call edges | Code-side evidence; panel: "call graph is not enough" needs a call graph to go beyond |
| M6 | Identity map and observed-implementer resolution | C4 |
| M7 | Acceptance-criteria splitting; semantic candidates (BM25 + one local embedding model, rank fusion, top-5, frozen) | Panel: "use semantic tools"; C9 |
| M8 | Typed trace links at requirement and criterion level | Core contribution 3 |
| M9 | Contradiction cases C1–C9 as pure rule functions | Core decision logic |
| M10 | Verdict truth table | Typed verdicts |
| M11 | Evidence bundle: canonical JSON, bundle and verdict hashes, file store, `replay` | Core contribution 4 |
| M12 | Pipeline orchestrator and CLI (`evaluate`, `replay`, `show`) | Runs the module end to end |
| M13 | Read-only REST API and web interface | Brief requires a live interface; rubric criterion 3 forbids screenshots |
| M14 | CI on every PR and CD on `main` (image → registry → deploy → smoke test) | Brief requires CI **and** CD |
| M15 | Sandbox: Jira `SBX` tickets and `codeatlas-sandbox` repository with one PR per scenario | Live demo with edge and failure cases |

**SHOULD HAVE** (only after all MUST items pass their gate)

| ID | Capability | Why not MUST |
| --- | --- | --- |
| S1 | Per-test coverage collection on the sandbox so C7 can return VIOLATED instead of INSUFFICIENT\_EVIDENCE | Needs the target's test suite to run inside CodeAtlas; C7 is honest without it |
| S2 | Post the verdict as a GitHub check run | Nice integration; the web interface already shows the verdict |
| S3 | Route handler extraction (FastAPI/Flask decorators) as a derived-link source | Adds evidence quality, not new capability |
| S4 | Baseline spike: BM25 vs embeddings on \~50 mined issue→PR pairs | First evidence for the final-system ML track; report only |

**DEFERRED** (named in the report as future iterations)

| Capability | Why deferred |
| --- | --- |
| Learned evidence fusion, controlled-error abstention, requirement-change classification | Needs a labelled dataset that does not exist yet (Iterations 3–4) |
| Evaluation dataset, baselines, ablations, NFR-1 to NFR-6 measurements | Same dependency on labelled data |
| Entity identity across renames and moves (FR-1.4) | Hard; not needed to show the contribution |
| Incremental invalidation (FR-10) | Full evaluation of one PR takes seconds at sandbox size |
| Full inter-procedural call resolution (pyan3/JARVIS-level) | Direct calls are enough for derived links at mid |
| Linear adapter, GitHub App and webhooks, multi-repository | Breadth, not depth |
| User accounts and login for the web interface | Read-only demo data; no personal data shown beyond the sandbox |

**REJECTED**

| Capability | Why rejected |
| --- | --- |
| Graph database (FR-2.8) | One evaluation is small; a database adds operations and slows replay for no gain |
| Message queues, workers, microservices | No concurrent load; one process is the right size |
| Recommendation engine: smells, dead code, bug patterns (FR-9) | Already solved by Ruff, Vulture, SonarQube; unrelated to the contribution |
| Licence and vulnerability evidence (FR-7.3–7.6) | Solved by existing scanners; drifts away from requirement traceability |
| Read-only agent API (FR-8.4), Jira plugin | No user need shown; adds surface without evidence value |
| Any language model on the verdict path; hosted embedding APIs | Breaks determinism and replay; sends code to third parties |
| Writing to Jira or GitHub repositories | NFR-7: CodeAtlas is read-only |

## 4. Architecture

CodeAtlas is a single Python package arranged in six layers with one rule: **dependencies point inward, and nothing after the freeze step touches the network.** The split between an online collection half and an offline evaluation half is what makes verdicts replayable.

&#91;embedded content: architecture · 3 sources, 4 layers, 2 interfaces\]

Only `collect/` touches the network; everything below the dashed line is pure and runs from the frozen bundle, which is why any verdict can be replayed and checked.

| Layer | Package | Allowed to import | Side effects |
| --- | --- | --- | --- |
| 1. Domain | `codeatlas.schema` | Standard library, Pydantic | None |
| 2. Collectors | `codeatlas.collect.{jira,github,git}` | Domain, config | Network and disk reads; raw-response cache |
| 3. Analysis | `codeatlas.analyze` | Domain, config | None, except the embedding model call in `semantic` (collection time only) |
| 4. Bundle | `codeatlas.bundle` | Domain | Writes bundles to the store |
| 5. Evaluation | `codeatlas.evaluate` | Domain only | None: pure functions over a frozen bundle |
| 6. Application | `codeatlas.pipeline`, `cli`, `api`, `web` | All layers | Orchestration, HTTP, files |

**Two hard boundaries, enforced in CI by `import-linter` contracts.**

1. `codeatlas.evaluate` may import only `codeatlas.schema`. Rules therefore cannot fetch data or read the clock; they can only judge what is in the bundle.
2. `codeatlas.api` and `codeatlas.web` may not import `codeatlas.collect`. The deployed web service only reads stored bundles; it never calls Jira, GitHub or a model, so the deployment needs no secrets and cannot change a verdict.

**Why each decision.**

| Decision | Reason | Alternative rejected |
| --- | --- | --- |
| Monolith package, one process | Three developers, one evaluation at a time, seconds per run | Microservices, queues: operational cost with no load to justify it |
| Pydantic models as the only contract between layers | Validation at every boundary; JSON Schema export for the report | Plain dicts: no validation, silent drift between teammates |
| Evidence bundle as a JSON file, content-addressed by its hash | Replay needs an immutable, portable record; files are easy to inspect, diff and commit as fixtures | Graph or SQL database: harder to version and replay |
| In-memory graph (`networkx`) built per evaluation | Hundreds of nodes; traversals are trivial | Neo4j (FR-2.8) |
| Collectors behind small protocols (`WorkItemSource`, `ChangeSource`) | Tests use fixtures; Linear can be added later without touching rules | Calling `httpx` directly from rules |
| Server-rendered web pages (FastAPI + Jinja2) with light JavaScript | No separate frontend build or Node toolchain in CI; enough for a read-only interface | React single-page app: extra build, extra failure modes |
| Local embedding model, scores frozen into the bundle | Deterministic replay; no code leaves the machine | Hosted embedding API |

**Where it runs.** Evaluation runs from the CLI on a developer machine or in GitHub Actions, where credentials exist. The deployed container serves the web interface and API over the stored bundles, including all demo scenarios.

## 5. Main module: the evaluation pipeline

**Responsibility.** Given one pull request in one repository, produce a frozen evidence bundle and a verdict over it. **Input:** repository (local path + GitHub `owner/name`), PR number, policy. **Output:** `EvidenceBundle` + `Verdict`, written to the bundle store; CLI exit code reflects the verdict.

Stages 1–10 run online (collection); stages 11–13 run offline and are exactly what `replay` re-executes.

| # | Stage | Input | Processing | Output | Module | On failure |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Resolve change | repo path, `owner/name`, PR number | GitHub PR lookup; `git fetch`; compute merge-base | `ChangeRef` (base, head, merge-base SHAs) | `collect.github`, `collect.git` | Abort, exit 3 |
| 2 | Load policy | base SHA | Read `.codeatlas/policy.yaml` and `identity_map.yaml` **at base** | `Policy`, `IdentityMap` | `collect.git` | Missing policy → built-in defaults, recorded as a finding |
| 3 | Extract work-item keys | branch, PR title/body, commit messages | Regex from policy | `WorkItemRef[]` with where each key was found | `analyze.keys` | No key → continue; requirement cases become INSUFFICIENT |
| 4 | Collect requirements | keys | Jira issue + paginated changelog + group members; raw responses cached by SHA-256 | `RequirementTimeline` per key | `collect.jira` | Jira unreachable → abort, exit 3. Key not found → `WorkItemMissing` evidence, continue |
| 5 | Rebuild versions and lifecycle | timelines, policy | Walk changelog backwards; chain check; approvals, stale approvals, priority and assignee history | `RequirementVersion[]`, `LifecycleFacts` | `analyze.lifecycle` | Chain mismatch → version contents UNRESOLVED; change events kept |
| 6 | Governance snapshot | base and head trees, diff | Parse CODEOWNERS, ADR front matter at base; match changed paths to `governance_paths` | `GovernanceSnapshot`, `GovernanceChange[]` | `collect.git`, `analyze.governance` | Unparseable file → finding, file ignored |
| 7 | Reviews and identities | PR | Reviews with reviewed commit; resolve commit authors and reviewers through identity map | `Review[]`, `ResolvedPerson[]` | `collect.github`, `analyze.identity` | Unmapped person → UNRESOLVED, never a mismatch |
| 8 | Code analysis | base and head files | Tree-sitter parse; hunks → enclosing entities; module imports; direct call edges | `CodeEntity[]`, `ChangedEntity[]`, `CodeEdge[]` | `analyze.code` | Parse error → entity UNRESOLVED, continue |
| 9 | Acceptance criteria | requirement versions | Split into items (bullets, then sentences) | `Criterion[]` | `analyze.criteria` | No usable text → criterion list empty, flagged |
| 10 | Semantic candidates | criteria, changed + neighbouring entities | BM25 and one pinned local embedding model; reciprocal rank fusion; keep top-5 per criterion with ranks and scores | `SemanticCandidate[]` | `analyze.semantic` | Model unavailable → stage marked unavailable; C9 INSUFFICIENT |
| 11 | Freeze | all of the above | Canonical JSON; bundle hash; write to store | `EvidenceBundle` | `bundle` | Validation error → nothing stored, exit 4 |
| 12 | Link typing | bundle | Assign one of five states to each requirement→entity link | `TraceLink[]` | `evaluate.links` | — (pure) |
| 13 | Rules and verdict | bundle, links, policy | C1–C9 → `CheckResult[]`; truth table → `Verdict`; verdict hash | `Verdict` | `evaluate` | — (pure) |

**Exit codes.** 0 PASS, 1 FAIL, 2 REVIEW or UNKNOWN, 3 collection error, 4 invalid evidence, 5 replay mismatch. CI can therefore gate on the verdict without parsing output.

**Never partial.** A bundle is written only when stages 1–11 all complete. A collection error leaves no bundle behind, so the store never holds half-collected evidence.

## 6. Component breakdown

Twenty components, each with one owner and an explicit "does not do" boundary. Contracts for the types named here are in section 7.

| Component | Responsibility | Inputs → outputs | Depends on | Does NOT do |
| --- | --- | --- | --- | --- |
| `schema` | All domain models and enums; JSON Schema export | — → Pydantic models | Pydantic | Any I/O or business logic |
| `config` | Load runtime settings from environment; load and validate `policy.yaml` and `identity_map.yaml` | env, YAML text → `Settings`, `Policy`, `IdentityMap` | schema, PyYAML | Read files from the working tree directly (git layer supplies base-commit text) |
| `collect.jira` | Read-only Jira REST v3 client: issue, paginated changelog, group members; retry on 429; raw cache | key → `JiraIssueRaw`, `JiraChangelogRaw`, `GroupMembers` | httpx, config | Interpret approvals or versions; write to Jira |
| `collect.github` | Read-only GitHub REST client: PR, commits, reviews | owner/name, PR number → `PullRequestRaw`, `Review[]`, `Commit[]` | githubkit or httpx | Post comments; write anything (check run is SHOULD S2, separate module) |
| `collect.git` | Local repository access with pygit2: merge-base, diff hunks, file text at any commit | repo path, SHAs → `DiffHunk[]`, file bytes | pygit2 | Checkout or modify the working tree |
| `analyze.keys` | Extract work-item keys and record where each was found | branch, title, body, messages → `WorkItemRef[]` | schema | Call Jira |
| `analyze.lifecycle` | Rebuild requirement versions; chain check; approvals, stale approvals, priority and assignee history | raw issue + changelog, policy → `RequirementVersion[]`, `LifecycleFacts` | schema | Decide verdicts |
| `analyze.governance` | Parse CODEOWNERS (GitHub semantics) and ADR front matter; match changed paths to governance paths | base-commit file text, diff → `GovernanceSnapshot`, `GovernanceChange[]` | schema | Read governance from the head branch |
| `analyze.identity` | Resolve Git authors and GitHub logins to Jira accounts | identity map, people → `ResolvedPerson[]` | schema | Guess by name similarity |
| `analyze.code` | Tree-sitter parse; changed entities; module imports; direct call edges | file text, hunks → `CodeEntity[]`, `ChangedEntity[]`, `CodeEdge[]` | tree-sitter | Inter-procedural or dynamic call resolution; rename tracking |
| `analyze.criteria` | Split acceptance criteria into items | requirement versions → `Criterion[]` | schema | Rewrite or summarise requirement text |
| `analyze.semantic` | BM25 + local embeddings + rank fusion; top-5 per criterion | criteria, entities → `SemanticCandidate[]` | rank-bm25, sentence-transformers | Decide whether a link is true |
| `bundle` | Canonical JSON, bundle hash, verdict hash, file store, replay | collected evidence → `EvidenceBundle`; bundle → replay result | schema, orjson | Network access; re-running models |
| `evaluate.links` | Assign link states from explicit, structural and semantic evidence | bundle → `TraceLink[]` | schema | Any I/O |
| `evaluate.rules` | C1–C9 as pure functions | bundle, links, policy → `CheckResult` | schema | Any I/O, clock or randomness |
| `evaluate.verdict` | Apply the truth table; compute verdict hash | `CheckResult[]` → `Verdict` | schema | Weight or score findings |
| `pipeline` | Run stages 1–13 in order; never store partial bundles | `EvaluateRequest` → `EvaluationResult` | all layers | Contain business rules |
| `cli` | `evaluate`, `replay`, `show`, `version` commands; exit codes | arguments → printed result, exit code | pipeline, bundle | Business logic |
| `api` | Read-only JSON endpoints over the bundle store | HTTP → JSON | bundle, evaluate | Collect evidence; call Jira, GitHub or a model |
| `web` | Server-rendered pages: case list, case detail, requirement timeline, trace links, replay | API data → HTML | api | Edit anything |

## 7. Data and interface contracts

These contracts are frozen at Gate G1 (section 9) as `schema-v0`. After that, a change needs approval from all three members and must update fixtures and sample bundles in the same pull request. All models are Pydantic v2, `frozen=True`, with UTC-aware datetimes.

### 7.1 Domain models (`codeatlas.schema`)

```python
CaseId = Literal["C1","C2","C3","C4","C5","C6","C7","C8","C9"]
Outcome = Literal["SATISFIED","VIOLATED","INSUFFICIENT_EVIDENCE","NOT_APPLICABLE"]
LinkState = Literal["OBSERVED","DETERMINISTICALLY_DERIVED","SEMANTIC_CANDIDATE","CONFLICTING","UNRESOLVED"]
VerdictValue = Literal["PASS","FAIL","REVIEW","UNKNOWN"]

class Evidence(BaseModel):            # base for every fact in a bundle
    evidence_id: str                   # e.g. "jira:SBX-3:history:10045:acceptance_criteria"
    source: Literal["jira","github","git","analysis"]
    source_ref: str                    # URL, or "path@sha:line"
    retrieved_at: datetime
    extractor_version: str

class ChangeRef(BaseModel):
    repo: str                          # "owner/name"
    pr_number: int
    base_sha: str; head_sha: str; merge_base_sha: str

class WorkItemRef(Evidence):
    key: str
    found_in: Literal["branch","pr_title","pr_body","commit"]
    commit_sha: str | None

class ChangeEvent(Evidence):           # one changed field in one Jira history entry
    key: str; history_id: str; field: str
    from_value: str | None; to_value: str | None
    actor_account_id: str | None       # None = Jira automation
    at: datetime

class RequirementVersion(BaseModel):
    key: str; version_no: int; valid_from: datetime; valid_to: datetime | None
    summary: str | None; description: str | None; acceptance_criteria: str | None
    status: str; priority: str | None; assignee_account_id: str | None
    content_resolved: bool             # False if the chain check failed
    unresolved_reasons: list[str]

class Approval(BaseModel):
    key: str; actor_account_id: str | None; at: datetime; history_id: str
    by_approver_group: bool; revoked_at: datetime | None

class PriorityChange(BaseModel):
    key: str; actor_account_id: str | None; at: datetime
    from_priority: str | None; to_priority: str | None
    after_first_approval: bool; actor_has_authority: bool | None   # None = system actor

class LifecycleFacts(BaseModel):
    key: str; requester_account_id: str | None
    approvals: list[Approval]; latest_valid_approval_at: datetime | None
    last_requirement_edit_at: datetime | None
    priority_changes: list[PriorityChange]
    assignee_history: list[tuple[datetime, str | None]]

class Commit(Evidence):
    sha: str; author_email: str; author_login: str | None
    committed_at: datetime; message: str

class Review(Evidence):
    reviewer_login: str; state: Literal["APPROVED","CHANGES_REQUESTED","COMMENTED","DISMISSED"]
    commit_id: str; submitted_at: datetime

class ResolvedPerson(BaseModel):
    git_email: str | None; github_login: str | None
    jira_account_id: str | None        # None = UNRESOLVED

class OwnerRule(BaseModel):
    pattern: str; owners: list[str]; line: int

class GovernanceSnapshot(Evidence):
    base_sha: str; codeowners_path: str | None
    owner_rules: list[OwnerRule]; adrs: list["Adr"]; policy_blob_sha: str | None

class GovernanceChange(Evidence):
    path: str; change_type: Literal["added","modified","deleted","renamed"]

class CodeEntity(BaseModel):
    entity_id: str                     # "pkg.module.Class.method"
    kind: Literal["module","class","function","method"]
    file: str; start_line: int; end_line: int
    signature: str | None; docstring: str | None

class ChangedEntity(Evidence):
    entity_id: str | None              # None when a hunk could not be mapped
    change: Literal["added","modified","deleted","unresolved"]
    file: str; hunk_lines: tuple[int, int]
    commit_shas: list[str]             # commits that touched it

class CodeEdge(BaseModel):
    src: str; dst: str; kind: Literal["IMPORTS","CALLS"]; confident: bool

class Criterion(BaseModel):
    criterion_id: str                  # "SBX-3:v2:ac1"
    key: str; version_no: int; text: str; derived_from_description: bool

class SemanticCandidate(BaseModel):
    criterion_id: str; entity_id: str
    bm25_rank: int | None; dense_rank: int | None; fused_rank: int
    fused_score: float                 # rounded to 4 decimals before hashing
    model_id: str                      # "<name>@<revision>"

class TraceLink(BaseModel):
    requirement_key: str; criterion_id: str | None; entity_id: str
    state: LinkState; evidence_ids: list[str]; reasons: list[str]

class CheckResult(BaseModel):
    case: CaseId; outcome: Outcome
    severity: Literal["block","review","none"]; required: bool
    message: str; evidence_ids: list[str]

class Verdict(BaseModel):
    value: VerdictValue; results: list[CheckResult]
    ruleset_version: str; bundle_hash: str; verdict_hash: str

class EvidenceBundle(BaseModel):
    schema_version: Literal["0"]
    change: ChangeRef; codeatlas_version: str
    extractor_versions: dict[str, str]; model_ids: list[str]
    work_items: list[WorkItemRef]; change_events: list[ChangeEvent]
    versions: list[RequirementVersion]; lifecycle: list[LifecycleFacts]
    commits: list[Commit]; reviews: list[Review]; people: list[ResolvedPerson]
    governance: GovernanceSnapshot; governance_changes: list[GovernanceChange]
    entities: list[CodeEntity]; changed: list[ChangedEntity]; edges: list[CodeEdge]
    criteria: list[Criterion]; candidates: list[SemanticCandidate]
    collection_notes: list[str]        # e.g. "work item SBX-99 not found"
```

### 7.2 Collector and rule interfaces

```python
class WorkItemSource(Protocol):
    def fetch_issue(self, key: str) -> dict: ...            # raw Jira JSON
    def fetch_changelog(self, key: str) -> list[dict]: ...  # all pages, oldest first
    def group_members(self, group: str) -> set[str]: ...    # account IDs

class ChangeSource(Protocol):
    def pull_request(self, repo: str, number: int) -> dict: ...
    def commits(self, repo: str, number: int) -> list[dict]: ...
    def reviews(self, repo: str, number: int) -> list[dict]: ...

class Rule(Protocol):
    case: CaseId
    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult: ...
```

Each source has a live implementation and a fixture implementation that reads recorded JSON from `tests/fixtures/`. The pipeline receives sources by injection, so every test runs without network.

### 7.3 Link-state assignment (`evaluate.links`), first match wins

1. **CONFLICTING:** the entity was changed by a commit carrying key A, but the PR also links key B and the entity is a top-1 semantic candidate only for B's criteria.
2. **OBSERVED:** a commit carrying the requirement's key modified the entity.
3. **DETERMINISTICALLY\_DERIVED:** the entity is reached by one confident `CALLS` or `IMPORTS` edge from an OBSERVED entity, or lies inside the scope of an ADR the requirement links.
4. **SEMANTIC\_CANDIDATE:** the entity is in the top-5 for one of the requirement's criteria and none of the above applies.
5. **UNRESOLVED:** none of the above.

Semantic evidence never upgrades a state. A criterion-level link inherits the requirement-level state and records whether the criterion was chosen explicitly or semantically.

### 7.4 Verdict truth table and hashes

| Order | Condition | Verdict |
| --- | --- | --- |
| 1 | Any VIOLATED result with severity block | FAIL |
| 2 | Any VIOLATED result with severity review | REVIEW |
| 3 | Any required result with INSUFFICIENT\_EVIDENCE | UNKNOWN |
| 4 | Otherwise | PASS |

```latex
\text{bundle\_hash} = \mathrm{SHA256}\big(\mathrm{canonical}(\text{bundle without bundle\_hash})\big)
```

```latex
\text{verdict\_hash} = \mathrm{SHA256}\big(\text{bundle\_hash} \,\Vert\, \text{ruleset\_version} \,\Vert\, \mathrm{canonical}(\text{results}, \text{value})\big)
```

Canonical form: UTF-8 JSON, sorted keys, no extra whitespace, datetimes as ISO-8601 UTC to the second with `Z`, every list sorted by its identifier, floats rounded to 4 decimals and written as strings.

### 7.5 Configuration files (read at the base commit of the evaluated repository)

```yaml
# .codeatlas/policy.yaml
version: 1
workitem_key_pattern: "[A-Z]+-\\d+"
requirement_fields: [summary, description, acceptance_criteria]
governance_paths: [CODEOWNERS, .github/CODEOWNERS, docs/CODEOWNERS, docs/adr/**, .codeatlas/**, .github/workflows/**, .pre-commit-config.yaml]
approval: { status: Approved, approver_group: sbx-requirement-approvers }
priority_authority_group: sbx-priority-authority
cases:
  C3: { severity: block }
  C5: { severity: block }
  C7: { required_when_label: requires-test-evidence }
semantic: { top_k: 5 }
```

```yaml
# .codeatlas/identity_map.yaml
people:
  - jira_account_id: "712020:..."
    github_login: areej8
    git_emails: [areejhamid8560@gmail.com]
```

### 7.6 REST API (`codeatlas.api`, read-only)

| Method and path | Returns |
| --- | --- |
| `GET /health` | `{"status": "ok", "version": "0.2.0"}` |
| `GET /api/cases` | List of `{bundle_hash, repo, pr_number, keys, verdict, evaluated_at}`, newest first |
| `GET /api/cases/{bundle_hash}` | Change, verdict, every `CheckResult` with linked evidence summaries |
| `GET /api/cases/{bundle_hash}/requirements/{key}` | Versions, change events and lifecycle facts for one requirement |
| `GET /api/cases/{bundle_hash}/links` | Trace links and semantic candidates grouped by criterion |
| `GET /api/cases/{bundle_hash}/replay` | `{stored_verdict_hash, recomputed_verdict_hash, match}` |

Errors use one shape: `{"error": {"code": "case_not_found", "message": "...", "details": {}}}` with HTTP 404, 422 or 500. Codes: `case_not_found`, `requirement_not_in_case`, `bundle_corrupt` (hash mismatch), `internal_error`.

### 7.7 CLI

```text
codeatlas evaluate --repo PATH --github OWNER/NAME --pr N [--store DIR] [--fixtures DIR]
codeatlas replay BUNDLE_PATH
codeatlas show BUNDLE_PATH [--json]
codeatlas version
```

### 7.8 Errors (`codeatlas.errors`)

```python
class CodeAtlasError(Exception): ...
class ConfigError(CodeAtlasError): ...                  # invalid policy, identity map or settings
class CollectionError(CodeAtlasError):                 # Jira or GitHub failure
    source: str; status: int | None; retryable: bool
class EvidenceValidationError(CodeAtlasError): ...      # bundle fails schema validation
class ReplayMismatchError(CodeAtlasError): ...          # stored and recomputed hashes differ
```

## 8. Repository structure

Target layout. Files marked **(exists)** are already on `main` and are kept; everything else is new. The existing `schema/` folder is kept and becomes the home of the contracts in section 7. No folder may be created that is not listed here without a spec change.

```
codeatlas/
├─ .github/
│  ├─ workflows/ci.yml            (exists, rewritten in Phase 3: section 12)
│  ├─ workflows/cd.yml            new: build → GHCR → deploy → smoke
│  ├─ pull_request_template.md    new: issue link, tests run, AI-use checklist
│  └─ ISSUE_TEMPLATE/{feature,bug}.md
├─ CODEOWNERS                     (exists, placeholders replaced with real logins)
├─ Dockerfile                     (exists)
├─ pyproject.toml / uv.lock       (exists)
├─ .pre-commit-config.yaml        new
├─ .importlinter                  new: layer contracts (section 4)
├─ config/
│  ├─ policy.example.yaml         case thresholds, label names, group names
│  └─ identity_map.example.yaml   git email / GitHub login → Jira accountId (fake values)
├─ src/codeatlas/
│  ├─ schema/                     (exists) Pydantic v2 models, schema_version "0"
│  ├─ config.py  errors.py  logging.py
│  ├─ collect/                    ONLINE layer, the only code allowed to do network I/O
│  │  ├─ jira/{client.py, adf.py, recorder.py}
│  │  ├─ github/{client.py, recorder.py}
│  │  └─ git/{repo.py}            pygit2: diff, blame, files at base commit
│  ├─ analyze/                    PURE, no I/O
│  │  ├─ keys.py  lifecycle.py  governance.py  identity.py
│  │  ├─ code.py                  tree-sitter entities + import / call edges
│  │  ├─ criteria.py  semantic.py
│  ├─ bundle/{canonical.py, hashing.py, store.py, replay.py}
│  ├─ evaluate/                   PURE, no I/O, no clock, no randomness
│  │  ├─ links.py  verdict.py
│  │  └─ rules/{base.py, c1_changed_after_impl.py … c9_unexplained_change.py}
│  ├─ pipeline.py                 orchestrates the 13 stages (section 5)
│  ├─ cli.py                      (exists, extended)
│  ├─ api/{app.py, routes.py}     (app.py exists)
│  └─ web/{templates/, static/}
├─ tests/
│  ├─ unit/  integration/  e2e/  property/
│  └─ fixtures/{jira/, github/, repos/, bundles/}   sanitised recordings only
├─ scripts/check_commit_msgs.py   (exists, changed to issue-number convention)
└─ docs/
   ├─ specs/                      one spec per issue before code (section 14)
   ├─ decisions/                  ADR-0001 … (stack, layering, hashing, identity)
   ├─ setup/                      Jira sandbox, GitHub sandbox, local run, deploy
   └─ report/                     FR/NFR list, diagrams, Panel Action Register, GenAI disclosure
```

Rules:

- `.coverage`, `.env`, `bundles/` output and any recording containing real data are git-ignored. Remove the tracked `.coverage` in the first Phase 1 PR.
- `collect/` is the only package that may import `httpx`, `githubkit` or `pygit2`. `analyze/` and `evaluate/` may import only `schema/`, the standard library, and their own pure dependencies (tree-sitter, rank-bm25, networkx, sentence-transformers inside `semantic.py` only). import-linter enforces this in CI.
- Fixtures are produced by the recorders and then sanitised; they are never hand-written to make a test pass.

## 9. Developer ownership and phases

The prompt asks for sequential phases Areej → Yusra → Saleha, ordered by architectural dependency. That order is correct for **what depends on what**, but a strictly sequential handover cannot fit 8 days and would leave two members with no commits for most of the period, which the rubric marks down (individual contribution is assessed per member). Resolution: **sequential gates, staggered starts.** Each phase may only *merge* into `develop` after the previous gate, but the next owner starts the work that does not depend on the gate (sandbox setup, recordings, UI on sample bundles) as soon as their phase opens.

| Phase | Owner | Window | Owns (code + tests) | Cannot merge before |
| --- | --- | --- | --- | --- |
| 1 · Core spine | Areej | Oct 7 – Oct 10 | schema v0; config, errors, logging; bundle canonical form, hashing, store, replay; Jira collector + ADF + recorder; lifecycle reconstruction; rule framework + verdict truth table; C1, C3, C6, C8, C7 stub; pipeline skeleton; CLI `collect / evaluate / replay` | — |
| 2 · Code and governance evidence | Yusra | Oct 8 – Oct 12 | GitHub collector + recorder; git repo adapter; governance at base commit; C2, C5; tree-sitter code analysis; identity map + C4; criteria extraction, semantic candidates, link states, C9; full C7 | G1 |
| 3 · Product surface and delivery | Saleha | Oct 9 – Oct 14 | FastAPI routes + Jinja2 UI; `cd.yml`, GHCR, deploy, smoke; CI hardening (import-linter, determinism job); E2E acceptance suite and scenario matrix; README, setup docs, diagrams, FR/NFR list, Panel Action Register, GenAI disclosure | G1 for UI on sample bundles; G2 for live wiring |

**Gates**

- **G1, schema and rule interface frozen (target Oct 9 morning).** `schema/` models, `WorkItemSource`, `ChangeSource` and `Rule` protocols merged; one sample bundle committed under `tests/fixtures/bundles/`; replay of that bundle is byte-identical in CI. After G1 a schema change needs an issue, a `schema_version` note and approval from all three owners.
- **G2, all scenarios correct from fixtures (target Oct 12 evening).** Every SBX scenario in section 18 evaluates to its expected verdict from recorded fixtures with the network blocked.
- **G3, release candidate (Oct 14).** CD deploy green, smoke test green, acceptance matrix green, `v0.1.0-mid` tagged on `main`. Code freeze after the tag; Oct 15 is demo only.

**Early work that does not wait for a gate**

- Yusra, Oct 8: create the GitHub sandbox repo and the scenario PRs C1–C9 plus the edge cases, so recordings exist before the analysers.
- Saleha, Oct 9: page templates and routes against the committed sample bundle; `cd.yml` against the `/health` endpoint that already exists.
- Areej, after Oct 10: reviewer for Yusra's PRs on the rule framework; owner of the Jira sandbox data.

Every member reviews at least one other member's PRs each phase; review comments are part of the contribution evidence.

**Timeline**

```
            Oct 7   Oct 8   Oct 9   Oct 10  Oct 11  Oct 12  Oct 13  Oct 14  Oct 15
Areej       [===== Phase 1 core spine =====]  review / fixes ---------------->
Yusra               [sandbox][====== Phase 2 evidence + C2 C4 C5 C7 C9 ======]
Saleha                      [UI on sample][==== Phase 3 live wiring, CD, E2E =======]
Gates                       G1                      G2              G3 tag   DEMO
```

## 10. Implementation order

Each step is one GitHub issue and one PR (a step may need two PRs if it exceeds the size limit in section 11). Complexity: S ≤ half a day, M ≈ one day, L > one day. "Do not start" names the work that must wait for this step.

| # | Step | Owner | Prerequisites | Deliverables | Acceptance | Cx | Do not start until done |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Repo hygiene | Areej | — | untrack `.coverage`; `.gitignore`; pre-commit; `.importlinter`; commit-check script switched to `#<issue>` | CI green; a commit without `#n` fails the check | S | any feature PR |
| 2 | Schema v0 | Areej | 1 | all models in section 7; JSON round-trip tests; `schema_version="0"` | every model round-trips; unknown fields rejected | M | 3, 4, 7, 9, 13 |
| 3 | Canonical form + hashing + store + replay | Areej | 2 | `bundle/*`; sample bundle fixture | same bundle → same `bundle_hash` across 2 runs and 2 OS images; tampered byte → replay refuses (exit 5) | M | **G1** |
| 4 | Rule framework + verdict truth table | Areej | 2 | `Rule` protocol, registry, `verdict.py` | every row of the truth table has a test; property test: verdict independent of rule order | M | **G1**, all rules |
| — | **G1** | all | 2, 3, 4 | tag `g1` on `develop` | sample bundle replays byte-identical in CI | — | Phase 2 merges, UI routes |
| 5 | Jira collector + ADF + recorder | Areej | G1 | issue, changelog (paged), comments, group membership; recorder writes sanitised fixtures | integration tests on recordings with `pytest-socket` blocking network; 429 handled with backoff | M | 6 |
| 6 | Lifecycle reconstruction | Areej | 5 | `RequirementVersion` list, approvals, priority changes, chain check | Hypothesis: forward replay of reconstructed versions equals current field; broken chain → UNRESOLVED contents, events kept | L | C1, C3, C6, C8 |
| 7 | C1, C3, C6, C8, C7 stub | Areej | 4, 6 | four rules + stub | 4 fixture bundles per rule (pass, fail/review, unknown, edge) | M | — |
| 8 | Pipeline skeleton + CLI | Areej | 3, 5 | `collect`, `evaluate`, `replay` commands; exit codes 0–5 | `codeatlas evaluate --bundle x` offline works; no partial verdicts on failure | M | live UI wiring |
| 9 | GitHub collector + git adapter | Yusra | G1 | PR, commits, reviews, files, CODEOWNERS at base; pygit2 diff | recordings for every SBX PR; rate-limit headers respected | M | 10, 11 |
| 10 | Governance + C2, C5 | Yusra | 9 | CODEOWNERS last-match, ADR front matter, policy at base; two rules | last-match property test against GitHub's documented examples | M | — |
| 11 | Code analysis | Yusra | 9 | tree-sitter changed entities; import graph; direct call edges | golden tests on fixture repos; unsupported language → file-level entity, not crash | L | 13 |
| 12 | Identity map + C4 | Yusra | 9, 6 | resolver; unmapped → UNRESOLVED | C4 never FAILs on an unmapped author | S | — |
| 13 | Criteria + semantic + links + C9 + full C7 | Yusra | 11 | BM25 + pinned embedding + RRF; top-5 frozen in bundle; link states | semantic candidates never change a PASS/FAIL (test removes them and compares verdicts); model hash recorded | L | **G2** |
| — | **G2** | all | 7, 8, 10, 12, 13 | tag `g2` | full scenario matrix green from fixtures, network blocked | — | live demo rehearsal |
| 14 | API + web UI | Saleha | G1 (sample), G2 (live) | routes in section 7; pages: case list, case detail, evidence, replay | E2E: every page renders every scenario; no write calls to Jira/GitHub | L | — |
| 15 | CD + CI hardening | Saleha | 1 | `cd.yml`, GHCR, deploy, smoke; determinism and import-linter jobs | push to `main` deploys and smoke-tests `/health` and one replay | M | G3 |
| 16 | Acceptance suite + docs | Saleha | G2 | `tests/e2e`; README; setup docs; diagrams; FR/NFR list; Panel Action Register; GenAI disclosure | every criterion in section 17 maps to a passing test or a manual check record | M | G3 |
| — | **G3** | all | 14, 15, 16 | tag `v0.1.0-mid` on `main` | CD green, matrix green | — | — |

## 11. Git workflow

**Task tracking:** GitHub Issues + one GitHub Project board (Todo → In progress → In review → Done). Jira is **not** used for team tasks; the Jira site exists only as the system CodeAtlas reads from (decision D-table, section 0). Each step in section 10 is an issue with the owner assigned and the step's acceptance text pasted in.

**Branches**

- `main`: released, deployable; protected; only merges from `develop` (or `hotfix/*`) via PR; every merge is tagged or deploys.
- `develop`: integration; protected; PRs from feature branches only.
- `feature/<issue#>-<short-name>`, e.g. `feature/12-lifecycle-chain-check`. `fix/<issue#>-…`, `docs/<issue#>-…`, `hotfix/<issue#>-…`.
- Existing branches `feature/CA-1-repo-setup` and `feature/CA-3-approval-convention`: merge or close them in step 1, then stop using `CA-` keys.

**Commits:** Conventional Commits with the issue number: `feat(lifecycle): reconstruct versions from changelog (#12)`. `scripts/check_commit_msgs.py` is changed to require `(#n)` and a type prefix. Author email must be the member's own GitHub account email so contribution graphs are correct; no shared accounts, no committing on someone else's behalf.

**Pull requests**

- Target size ≤ 400 changed lines excluding fixtures and lock files; larger needs a reason in the description.
- Template fields: linked issue (`Closes #n`), what changed, tests added, tests run locally, AI assistance used (section 14), screenshots for UI.
- Merge rule: CI green + one approval from someone other than the author + CODEOWNERS approval for owned paths. Squash merge into `develop`; merge commit from `develop` into `main` so release history stays visible.
- No force-push to `develop` or `main`. Branch protection set in step 1.

**Tags:** `g1`, `g2` on `develop` (lightweight, for evidence); `v0.1.0-mid` annotated on `main` with release notes generated from merged PRs.

## 12. CI/CD

The coordinator asks for CI **and** CD with automated testing. Deployment is justified here: the demo uses the deployed web interface, and a CD job that deploys and then smoke-tests is the cheapest proof that the product runs outside a laptop. If no host is available by Oct 12, CD still builds, pushes to GHCR and runs the smoke test against the container started inside the workflow; this fallback is declared in the report rather than hidden.

**`ci.yml`: on pull request to `develop` or `main`, and on push to `develop`**

| Job | Runs | Fails the build when |
| --- | --- | --- |
| lint | pre-commit (ruff check, ruff format --check, end-of-file, YAML), commit-message check | any finding |
| types | `mypy --strict src` | any error |
| architecture | `lint-imports` | a layer contract in section 4 is broken (e.g. `evaluate` imports `httpx`) |
| test | `pytest tests/unit tests/integration tests/property` with `pytest-socket` (network disabled), coverage report | a test fails; coverage of `analyze/`, `bundle/`, `evaluate/` < 85% |
| determinism | evaluate every bundle in `tests/fixtures/bundles` twice in separate processes; compare `verdict_hash`; replay with a tampered copy | hashes differ, or tampered replay does not exit 5 |
| build | `docker build` (no push) | image fails to build or `/health` fails inside it |

**`cd.yml`: on push to `main` and on tags `v*`**

1. Build image, tag with commit SHA and, on tags, the version.
2. Push to `ghcr.io/codeatlas-fyp/codeatlas` using `GITHUB_TOKEN` (packages: write).
3. Deploy to the chosen host (open decision O2) using a deploy secret stored in a GitHub Environment named `production` with required reviewer.
4. Smoke test against the deployed URL: `/health` returns the released version; `POST /api/replay` on a bundled fixture returns the expected `verdict_hash`.
5. On tags: create a GitHub Release with notes from merged PRs.

**Secrets:** Jira API token and GitHub token used by live collection are **not** in CI. CI runs only on recorded fixtures. The deployed app reads its tokens from the host's secret store; they never appear in logs, images or bundles.

**Branch protection:** both `develop` and `main` require lint, types, architecture, test, determinism and build to pass.

## 13. Testing strategy

The product's main claim is that a verdict is correct, explained, and reproducible. The tests are organised around proving those three things, not around a coverage number.

| Level | What it proves | Where | Network |
| --- | --- | --- | --- |
| Unit | each analyser and rule on small hand-built schema objects | `tests/unit` | blocked |
| Property (Hypothesis) | invariants: lifecycle forward-replay equals current value; CODEOWNERS last-match equals a reference implementation; verdict independent of rule order; canonical form stable under dict-key order; semantic removal never changes PASS/FAIL | `tests/property` | blocked |
| Integration | collectors parse real recorded Jira/GitHub responses; pipeline from recordings to bundle | `tests/integration` | blocked |
| Golden / determinism | each fixture bundle → expected verdict file; two runs → identical `verdict_hash` | `tests/fixtures/bundles` + CI job | blocked |
| E2E acceptance | the criteria in section 17, through CLI and HTTP | `tests/e2e` | blocked (fixtures) |
| Live check (manual, recorded) | live Jira + GitHub sandbox produces the same verdicts as fixtures | `docs/report/live-run-<date>.md` with bundle hashes | allowed, not in CI |

**Fixture rule per rule:** at least four bundles for each case C1–C9: one PASS, one FAIL or REVIEW, one UNKNOWN (missing evidence), one edge case named in the rule's spec. Expected verdicts are written in the spec **before** the rule is coded, and reviewed by someone other than the rule author.

**Scenario matrix:** a table in `docs/report/` listing every SBX scenario, expected verdict per case, actual verdict, bundle hash. Generated by a script from the E2E run; never typed by hand.

**Not tested at mid (stated, not hidden):** accuracy of semantic candidates against a labelled dataset (deferred to the final ML track); performance beyond the sandbox size; languages other than Python for tree-sitter entities; Jira Data Center.

**Forbidden:** tests that assert only that a function runs; mocking the unit under test; snapshot files regenerated to make a failing test pass; `xfail` without an issue number.

## 14. AI-assisted development rules

The team will use Claude Code for most implementation. These rules make that safe and make the human contribution visible. Copy this section into `CLAUDE.md` at the repo root in step 1 so the agent reads it every session.

**Workflow per issue (spec-first)**

1. The owner opens the issue and asks the agent for a spec only: `docs/specs/<issue#>-<name>.md` with purpose, inputs/outputs using section 7 types, acceptance criteria (Given/When/Then), fixture list with expected verdicts, files to touch, and open questions.
2. The owner reads, edits and commits the spec. **No code before the spec is committed.**
3. The agent writes tests from the spec, then code to pass them, on the feature branch.
4. The owner runs the tests locally, reads every changed line, and opens the PR.
5. A second member reviews against the spec, not against the agent's summary.

**The agent must not**

- invent API endpoints, fields or behaviour of Jira, GitHub, pygit2 or tree-sitter; when unsure it reads the official docs or a recorded response and cites which;
- invent requirements, cases, thresholds or schema fields not in this specification; if one seems needed, it stops and lists it as an open question;
- change `schema/` after G1, the truth table, or the layer contracts without an approved issue;
- write tests that only check that code runs, hand-write fixtures, edit a golden file to make a test pass, or weaken an assertion to go green;
- add a dependency not listed in section 4 without saying so;
- make one commit touching more than one issue, or a PR above the size limit without explanation;
- touch secrets, real personal data or `.env`; print tokens;
- call Jira or GitHub with write methods (CodeAtlas is read-only);
- push, merge, tag or close issues; humans do those.

**The agent must report at the end of every task**

- files changed and why;
- assumptions made;
- tests added; tests run and their result; tests **not** run and why;
- anything in the spec it could not satisfy;
- risks or follow-ups.

The owner pastes this report into the PR description.

**Disclosure:** the final report has a GenAI disclosure section listing tools used, what they were used for, and how output was verified. Each member must be able to explain, without the agent, any file they own in the demo Q&A.

## 15. Coding standards, configuration, logging and errors

**Code**

- Python 3.12, `uv` for env and lock; `ruff` (rules already in `pyproject.toml`), `mypy --strict`.
- All data crossing a module boundary is a section 7 Pydantic model; no raw dicts between layers. Models are frozen (`model_config = ConfigDict(frozen=True, extra="forbid")`).
- Pure functions in `analyze/` and `evaluate/`: no I/O, no `datetime.now()`, no randomness, no environment reads. Time comes from the bundle's `collected_at`.
- Timestamps are timezone-aware UTC (`DTZ` rules); ordering ties broken by id so results never depend on input order.
- One rule per file; each rule declares `case_id`, `required_evidence`, `version` and returns a `CheckResult` with reasons that cite evidence ids.
- Docstrings on public functions state inputs, output and the spec section they implement.

**Configuration** (`config.py`, pydantic-settings)

- Environment: `CODEATLAS_JIRA_BASE_URL`, `CODEATLAS_JIRA_EMAIL`, `CODEATLAS_JIRA_TOKEN`, `CODEATLAS_GITHUB_TOKEN`, `CODEATLAS_BUNDLE_DIR`, `CODEATLAS_LOG_LEVEL`, `CODEATLAS_EMBEDDING_MODEL`.
- Files: `policy.yaml` and `identity_map.yaml`, paths given on the CLI or via env. Both are copied into the bundle so replay never reads the live files.
- Missing or invalid config fails at start-up with exit code 4 (invalid input; code 2 is reserved for REVIEW/UNKNOWN) and a message naming the missing key.

**Logging** (`logging.py`)

- JSON lines to stdout: `ts`, `level`, `event`, `run_id`, `stage`, `case_id`, `duration_ms`.
- Every pipeline stage logs start and end with duration; the bundle stores the `run_id`.
- A redaction filter removes values of any key containing `token`, `authorization`, `password`, `secret`, and email addresses. A unit test feeds a token through the logger and asserts it is absent.

**Errors** (`errors.py`)

- `CodeAtlasError` base. Mapped to the exit codes in section 5: `SourceUnavailable` → 3; `ConfigError` and `EvidenceInvalid` → 4; `ReplayMismatch` (including a tampered bundle) → 5. A rule that raises is a bug: the run stores no verdict and exits 4 ("never partial").
- Collectors convert HTTP and git errors into these types; nothing above `collect/` sees `httpx` exceptions.
- Missing evidence is **data**, not an exception: it produces UNKNOWN or UNRESOLVED, never a crash.

## 16. Security and privacy

| Concern | Rule | How it is checked |
| --- | --- | --- |
| Credentials | Jira and GitHub tokens only in env / host secret store / GitHub Environment secrets; never in repo, fixtures, bundles, images or logs | `gitleaks` in pre-commit and CI; logger redaction test; `docker history` reviewed in step 15 |
| Least privilege | Jira: a dedicated sandbox account with Browse Projects only. GitHub: fine-grained token, read-only on the sandbox repo (contents, pull requests, metadata) | token scopes listed in `docs/setup/`; a test asserts collectors only issue GET requests |
| Read-only product | CodeAtlas never writes to Jira or GitHub at mid | HTTP client wrapper rejects non-GET methods; unit test |
| Personal data | Jira hides emails; CodeAtlas stores accountIds and display names only as present in responses. Recorder replaces names, emails and accountIds with consistent fakes (`user-01`, `user-01@example.test`) before a fixture is written | recorder unit test; reviewer checks every fixture PR |
| Bundle integrity | `bundle_hash` over canonical bytes; replay refuses on mismatch | determinism CI job |
| Untrusted input | PR titles, descriptions, ADF and code are data. No `eval`, no shell with interpolated input, YAML via `safe_load`, Jinja2 autoescape on | ruff `S` rules on `src/`; template test with `<script>` in a PR title |
| Web interface | Deployed demo is read-only; protected by HTTP basic auth or host access control; no endpoint triggers live collection without the auth | smoke test hits an unauthenticated route and expects 401 |
| Dependencies | locked via `uv.lock`; Dependabot alerts on | GitHub settings, step 1 |
| Embedding model | pinned name + revision + file hash recorded in the bundle; downloaded at build, not at request time | bundle field test |

## 17. Acceptance criteria

Each criterion becomes at least one test in `tests/e2e` or `tests/property` (ID in the test name, e.g. `test_AC07_...`). A criterion marked *manual* is checked once and recorded in `docs/report/`.

**Evidence and determinism**

- **AC01** Given a PR linked to an SBX Jira issue, when `codeatlas collect` runs, then a bundle is written whose `bundle_hash` equals the hash recomputed from its canonical bytes.
- **AC02** Given a stored bundle, when it is evaluated twice in separate processes, then both `verdict_hash` values are identical.
- **AC03** Given a bundle with one byte changed, when `codeatlas replay` runs, then it exits 5 and stores no verdict.
- **AC04** Given the network is disabled, when any stored bundle is evaluated, then evaluation succeeds (evaluation is offline).
- **AC05** Given Jira is unreachable during collection, then the command exits 3, writes no bundle, and logs `SourceUnavailable` without the token.

**Requirement lifecycle**

- **AC06** Given an issue whose description was edited three times, when lifecycle is reconstructed, then four versions exist and replaying the changes forward reproduces the current description.
- **AC07** Given a changelog whose `from` value does not match the next version's `to`, then the affected version contents are UNRESOLVED and every change event is still present.
- **AC08** Given an approval transition by a member of `sbx-requirement-approvers` followed by a description edit, then C3 returns FAIL with reason "stale approval" citing both evidence ids.

**Cases**

- **AC09** For each case C1–C9, given its four fixture bundles, then each bundle's result equals the expected result written in that case's spec.
- **AC10** Given a PR whose author email is not in the identity map, then C4 returns UNKNOWN with link state UNRESOLVED, never FAIL.
- **AC11** Given a PR that edits `CODEOWNERS` or `policy.yaml`, then governance is read at the base commit and C2 returns REVIEW.
- **AC12** Given a PR with no Jira key in branch, title or commits, then the verdict is UNKNOWN with reason "no work item", and no case returns PASS.

**Semantic evidence**

- **AC13** Given any bundle, when all SEMANTIC\_CANDIDATE links are removed, then no case's PASS/FAIL changes (only REVIEW/UNKNOWN may change).
- **AC14** Given the same bundle, then the frozen top-5 candidates per criterion are identical across runs and the model revision hash is recorded.

**Product surface**

- **AC15** Given the deployed app, when a user opens a case, then the page shows the verdict, each case result, its reasons, and links to the exact evidence items (Jira change, commit, review, file line).
- **AC16** Given a PR title containing `<script>`, then the page renders it as text.
- **AC17** Given a push to `main`, then CD builds, pushes, deploys and the smoke test passes (*verified from the Actions run*).
- **AC18** Given the repository at `v0.1.0-mid`, then CI is green and every member has merged PRs and reviews in the history (*manual*).

## 18. Mid-evaluation demo

**Setup:** Jira sandbox project `SBX` with issues prepared per scenario; GitHub sandbox repo with one PR per scenario; deployed CodeAtlas; laptop with the same image as fallback; all scenario bundles also recorded.

**Script (about 12 minutes, each member presents what they own)**

| # | Presenter | Show | What the panel should see |
| --- | --- | --- | --- |
| 1 | Areej | Problem in one slide, then architecture diagram (section 4) | collect → freeze → evaluate; why evaluation is offline |
| 2 | Areej | Live: run evaluation on the SBX PR for **C3 stale approval** from the web UI | Jira history reconstructed into versions; approval before the last edit → FAIL with both evidence links |
| 3 | Areej | **C1** and **C8** on their PRs | requirement changed after implementation → REVIEW; priority raised by non-authority → REVIEW |
| 4 | Yusra | **C2** governance PR and **C5** owner not approving head | CODEOWNERS read at the base commit, last-match rule shown |
| 5 | Yusra | **C9** and the semantic panel | changed entities, top-5 candidates per criterion, labelled as candidates that never decide PASS/FAIL |
| 6 | Yusra | Edge cases: unmapped author (C4 UNKNOWN), PR with no Jira key | missing evidence becomes UNKNOWN, not a guess |
| 7 | Saleha | Replay: same bundle evaluated again → same `verdict_hash`; tampered bundle → refused | reproducibility and integrity |
| 8 | Saleha | GitHub: issues board, branches, a PR with review, Actions CI run, CD run with smoke test, tag `v0.1.0-mid` | the CI/CD the coordinator asked for |
| 9 | Saleha | Scenario matrix and test summary | every scenario's expected vs actual, generated by the E2E run |

**Fallbacks:** Jira or GitHub unreachable → run the same scenarios from the recorded bundles in the UI and say so; deployed host down → local container from the same image.

**Documents to bring:** FR/NFR list with mid status, Panel Action Register (each defence comment → what changed → where), architecture and sequence diagrams, test summary, GenAI disclosure.

## 19. Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner |
| --- | --- | --- | --- | --- | --- |
| R1 | Phase 1 slips past Oct 9; G1 late blocks everyone | Medium | Critical | Schema and rule interface first (steps 2–4) before the Jira collector; Yusra and Saleha do gate-free prep | Areej |
| R2 | Jira Cloud changelog does not expose what lifecycle needs (e.g. description history truncated, group membership not readable with the sandbox token) | Medium | High | Spike on day 1: record one real issue's changelog and group API response before coding step 6; if a field is missing, the case becomes UNKNOWN and the spec is updated | Areej |
| R3 | AI-generated code that no one understands, or tests that prove nothing | High | High | Section 14 workflow; reviewer checks spec → tests → code; each member explains their files in Q&A | all |
| R4 | Contribution history uneven (all commits by one person, as now) | High | High | Staggered starts; own-account commits; each member reviews others' PRs | all |
| R5 | tree-sitter / embedding model makes the image large or slow to build | Medium | Medium | Python grammar only; small pinned model downloaded at build; CI caches | Yusra |
| R6 | No deploy host by Oct 12 | Medium | Medium | Fallback in section 12 (container smoke test inside CD) | Saleha |
| R7 | Rate limits or network at the venue | Medium | Medium | Recorded bundles; demo works offline | Saleha |
| R8 | Panel reads it as "hard-coded rules" | Medium | High | Lead the demo with lifecycle reconstruction, governance-at-base, identity resolution and replay integrity; rules are thin over those engines | Areej |
| R9 | Scope creep from the full FR list (graph DB, ML track) | Medium | Medium | Section 3 DEFERRED list; anything else needs an issue approved by all three | all |
| R10 | Real personal data leaks into fixtures | Low | High | Recorder fakes identities; gitleaks; fixture PR review | Areej |

## 20. Open decisions, blocking questions, assumptions and recommendations

**Open decisions** (owner decides by the date; until then the default applies)

| ID | Decision | Default | Decide by | Owner |
| --- | --- | --- | --- | --- |
| O1 | Interface shown as POC at the proposal defence (the coordinator requires the mid to use it or its improved version) | web UI (FastAPI + server-rendered pages) | Oct 8 | team |
| O2 | Deploy host | a free container host reachable over HTTPS; otherwise the CD fallback in section 12 | Oct 10 | Saleha |
| O3 | Embedding model | a small sentence-transformers model, pinned by revision | Oct 10 | Yusra |
| O4 | What happens to the CA tickets already created in Jira | close them with a note; they are not used for tracking | Oct 8 | Areej |
| O5 | Coverage threshold | 85% on `analyze/`, `bundle/`, `evaluate/` | G1 | team |

**Blocking questions**

1. **What interface did the panel see at the defence?** The slides say "Will present later". If it was a CLI only, the web UI is the "improved version"; if it was something else (e.g. a GitHub check or an extension) step 14 changes. This blocks step 14 only.
2. **Do the Jira sandbox token and plan allow reading group membership and full changelog?** Answered by the R2 spike on day 1. Blocks step 6 only.

**Assumptions**

- Mid-eval date 15 Oct 2026; code freeze 14 Oct.
- Jira Cloud (not Data Center); GitHub.com; one repo and one Jira project in scope.
- Python is the only language analysed by tree-sitter at mid.
- All three members can use Claude Code and have their own GitHub accounts.
- The rubric's "core module" is satisfied by one end-to-end path (PR → evidence → verdict → UI) that is live, tested and deployed.

**Recommendations**

- Do the R2 spike and step 1 today, before any feature code.
- Write `CLAUDE.md` from section 14 before the first agent session.
- Present engines first, cases second; the panel's "hobby project" concern is answered by lifecycle reconstruction, governance-at-base, replayable evidence and the CI/CD evidence, not by the number of cases.
- Keep a dated log in `docs/report/` of decisions and spikes; it doubles as evidence for the report.

## 21. Self-review

Checked against the request and the rubric.

| Check | Result |
| --- | --- |
| All 20 requested items present | Yes: sections 1–20, plus this review |
| Contradictions between documents identified, not followed blindly | Yes: D1–D14 in section 2 with the choice made |
| Coordinator requirement: core module live, using the POC interface or improved | Covered by steps 8 and 14; depends on O1 |
| Coordinator requirement: meaningful GitHub branches, CI and CD with automated tests | Sections 11 and 12; both workflows defined job by job |
| Each member has distinct, visible ownership | Section 9; one weakness: Phase 1 is front-loaded on Areej |
| Exit codes consistent across sections | Fixed during review: replay/tamper = 5, config error = 4 |
| Nothing invented about external APIs | Jira and GitHub behaviour needed by lifecycle and groups is flagged as a spike (R2) rather than assumed |
| Semantic evidence cannot decide PASS/FAIL | Stated in section 3, enforced by AC13 |
| Ready for "Implement Phase 1 according to the approved specification" | Yes for steps 1–8, once O4 and the R2 spike are done |

**Known weaknesses left in the spec**

- Eight days is tight for Phase 2 (steps 11 and 13 are L). If G2 slips, cut order is: C9 semantic panel UI polish → call edges (keep import graph) → C7 full (keep stub). C1–C6 and C8 are not cut.
- No labelled dataset at mid, so there is no accuracy number; the mid claims correctness on specified scenarios and reproducibility, not general accuracy.
- Sequential ownership gives Saleha the least core-logic code; the E2E suite and CD are substantial, but she should also own one rule fix or review cycle in Phase 2 to show engine understanding.
