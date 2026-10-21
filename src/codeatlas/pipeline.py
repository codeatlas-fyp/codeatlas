"""Pipeline orchestration (spec §5; step-8 spec).

`collect_bundle` runs the online stages and freezes them into an `EvidenceBundle`;
`evaluate_bundle` runs the offline stages (links, rules and verdict) and is also the evaluator
injected into `bundle.replay`.

Two ways to say which change is evaluated:

* a **pull request** (`CollectRequest.target` plus `ChangeSources`): PR metadata, commits and
  reviews come from GitHub; diffs, file contents, CODEOWNERS and ADRs come from a local clone at
  the PR's base and head commits; then governance, code, criteria and semantic analysis run;
* a **change file** (`CollectRequest.change`): the Phase-1 stand-in for GitHub and git. Only the
  Jira stages run, and the bundle says so in `collection_notes`.

Either way the Jira stages are the same, and nothing is stored unless collection completed.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from codeatlas import __version__
from codeatlas.analyze import code as code_analysis
from codeatlas.analyze import criteria as criteria_analysis
from codeatlas.analyze import governance as governance_analysis
from codeatlas.analyze import keys as keys_analysis
from codeatlas.analyze import semantic as semantic_analysis
from codeatlas.analyze.lifecycle import JiraHistory, LifecycleContext, TextForms, reconstruct
from codeatlas.bundle.hashing import effective_policy_hash, sha256_hex, verdict_hash
from codeatlas.bundle.store import BundleStore, load_bundle
from codeatlas.collect.git.repo import DiffHunk
from codeatlas.collect.jira.adf import adf_to_text, wiki_to_text
from codeatlas.config import ChangeFile
from codeatlas.errors import CollectionError, ConfigError, EvidenceValidationError
from codeatlas.evaluate.links import bundle_links
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.base import NOTE_APPROVERS_UNKNOWN, Rule
from codeatlas.evaluate.verdict import decide, run_rules
from codeatlas.logging import EventLog
from codeatlas.schema import (
    ChangedEntity,
    ChangeEvent,
    ChangeRef,
    CodeEdge,
    CodeEntity,
    Commit,
    Criterion,
    EvidenceBundle,
    GovernanceChange,
    GovernanceSnapshot,
    IdentityMap,
    LifecycleFacts,
    Policy,
    RequirementVersion,
    ResolvedPerson,
    Review,
    SemanticCandidate,
    TraceLink,
    Verdict,
    WorkItemRef,
    WorkItemSource,
)

# Change-file mode: every stage after Jira is missing, and the bundle must say so.
NOTE_NOT_COLLECTED = "governance, reviews, code and semantic evidence not collected (steps 9-13)"
TEXT_FORMS = TextForms(current=adf_to_text, history=wiki_to_text)

CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
ADR_FOLDER = "docs/adr"
POLICY_PATH = ".codeatlas/policy.yaml"
REVIEW_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"})


class JiraSource(WorkItemSource, Protocol):
    def fetch_status_categories(self, project_key: str) -> dict[str, str]: ...


class GitHubSource(Protocol):
    """Read-only GitHub REST calls; `GitHubClient` and the recorded `FixtureSource` satisfy it."""

    def pull_request(self, repo: str, number: int) -> dict[str, Any]: ...
    def commits(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def reviews(self, repo: str, number: int) -> list[dict[str, Any]]: ...


class GitSource(Protocol):
    """Read-only local-clone access; `collect.git.LocalRepo` satisfies it."""

    def merge_base(self, base_ref: str, head_sha: str) -> str: ...
    def diff_hunks(
        self, base_sha: str, head_sha: str, *, context_lines: int = 3
    ) -> list[DiffHunk]: ...
    def file_bytes(self, sha: str, path: str) -> bytes | None: ...
    def list_files(self, sha: str, prefix: str = "") -> list[str]: ...


@dataclass(frozen=True)
class PullRequestTarget:
    repo: str  # "owner/name"
    number: int


@dataclass(frozen=True)
class ChangeSources:
    """Where pull-request evidence comes from (GitHub API and a local clone of the repo)."""

    github: GitHubSource
    git: GitSource


@dataclass(frozen=True)
class CollectRequest:
    change: ChangeFile | None  # change-file mode; None when `target` is a pull request
    change_label: str  # where the change came from, e.g. "pr:owner/name#4" or a file name
    policy: Policy
    identity_map: IdentityMap
    run_id: str
    collected_at: datetime
    notes: list[str] = field(default_factory=list)
    target: PullRequestTarget | None = None
    embedding_model: str | None = None  # None: the semantic module's pinned default
    bm25_only: bool = False  # skip the embedding model (offline runs and tests)


def evaluate_bundle(
    bundle: EvidenceBundle,
    bundle_hash: str,
    rules: Iterable[Rule] | None = None,
    links: list[TraceLink] | None = None,
) -> Verdict:
    """Run the rules over a frozen bundle and seal the verdict with its hash.

    Links default to `bundle_links(bundle)`, computed from the bundle alone, so collect and
    replay always see the same links. A rule that raises is a bug: no verdict is produced and the
    error has exit code 4.
    """
    chosen = tuple(default_rules() if rules is None else rules)
    try:
        trace = bundle_links(bundle) if links is None else links
        results = run_rules(chosen, bundle, trace)
    except Exception as error:
        raise EvidenceValidationError(f"rule evaluation failed: {error}") from error
    value = decide(results)
    version = ruleset_version(chosen)
    return Verdict(
        value=value,
        results=results,
        ruleset_version=version,
        bundle_hash=bundle_hash,
        verdict_hash=verdict_hash(bundle_hash, version, results, value),
    )


def _members(
    source: JiraSource, group: str | None, ids: list[str], notes: list[str]
) -> frozenset[str] | None:
    """Account ids from the policy plus readable group members; None when nobody is known."""
    members = set(ids)
    group_readable = False
    if group:
        try:
            members |= source.group_members(group)
            group_readable = True
        except CollectionError as error:
            if error.status not in (403, 404):
                raise
            notes.append(f"group {group} not readable (HTTP {error.status})")
    return frozenset(members) if members or group_readable else None


# ---- change resolution ----------------------------------------------------------------------


@dataclass
class _Change:
    """What the rest of the pipeline needs to know about the change under evaluation."""

    ref: ChangeRef
    work_items: list[WorkItemRef]
    commits: list[Commit]
    reviews: list[Review] = field(default_factory=list)
    raw_commits: list[dict[str, Any]] = field(default_factory=list)


def _from_change_file(change: ChangeFile, label: str, at: datetime) -> _Change:
    ref = f"change-file:{label}"
    return _Change(
        ref=ChangeRef(
            repo=change.repo,
            pr_number=change.pr_number,
            base_sha=change.base_sha,
            head_sha=change.head_sha,
            merge_base_sha=change.merge_base_sha,
        ),
        work_items=[
            WorkItemRef(
                evidence_id=f"github:pr:{change.pr_number}:{item.found_in}:{item.key}",
                source="github",
                source_ref=ref,
                retrieved_at=at,
                extractor_version=__version__,
                key=item.key,
                found_in=item.found_in,
                commit_sha=item.commit_sha,
            )
            for item in change.work_items
        ],
        commits=[
            Commit(
                evidence_id=f"git:commit:{c.sha}",
                source="git",
                source_ref=f"{ref}@{c.sha}",
                retrieved_at=at,
                extractor_version=__version__,
                sha=c.sha,
                author_email=c.author_email,
                author_login=c.author_login,
                committed_at=c.committed_at,
                message=c.message,
            )
            for c in change.commits
        ],
    )


def _missing(source: str, what: str) -> CollectionError:
    return CollectionError(source, status=None, retryable=False, detail=what)


def _text(data: Any, *path: str) -> str | None:
    """`data[path[0]][path[1]]...` as a string, or None when any step is missing."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data if isinstance(data, str) and data else None


def _timestamp(value: str | None, what: str) -> datetime:
    if value is None:
        raise _missing("github", f"{what} has no timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _missing("github", f"{what} has an invalid timestamp {value!r}") from error
    if parsed.tzinfo is None:
        raise _missing("github", f"{what} timestamp {value!r} has no timezone")
    return parsed.astimezone(UTC)


def _from_pull_request(
    target: PullRequestTarget, sources: ChangeSources, policy: Policy, at: datetime
) -> _Change:
    """PR metadata, commits and reviews from GitHub; the merge base from the local clone."""
    repo, number = target.repo, target.number
    try:
        pr = sources.github.pull_request(repo, number)
        raw_commits = sources.github.commits(repo, number)
        raw_reviews = sources.github.reviews(repo, number)
    except (OSError, TypeError, ValueError) as error:  # recorded responses missing or malformed
        raise _missing("github", f"PR {repo}#{number}: {error}") from error
    base_sha = _text(pr, "base", "sha")
    head_sha = _text(pr, "head", "sha")
    if base_sha is None or head_sha is None:
        raise _missing("github", f"PR {repo}#{number} has no base or head commit SHA")
    try:
        merge_base_sha = sources.git.merge_base(base_sha, head_sha)
    except (KeyError, ValueError) as error:
        raise _missing(
            "git",
            f"commits {base_sha[:12]}/{head_sha[:12]} of PR #{number} are not in the local "
            "clone; run `git fetch origin` in the repo path",
        ) from error

    url = f"https://github.com/{repo}"
    commits: list[Commit] = []
    for raw in raw_commits:
        sha = raw.get("sha")
        if not isinstance(sha, str) or not sha:
            raise _missing("github", f"a commit of PR #{number} has no SHA")
        commits.append(
            Commit(
                evidence_id=f"git:commit:{sha}",
                source="github",
                source_ref=f"{url}/commit/{sha}",
                retrieved_at=at,
                extractor_version=__version__,
                sha=sha,
                author_email=_text(raw, "commit", "author", "email") or "",
                author_login=_text(raw, "author", "login"),
                committed_at=_timestamp(
                    _text(raw, "commit", "committer", "date")
                    or _text(raw, "commit", "author", "date"),
                    f"commit {sha[:12]}",
                ),
                message=_text(raw, "commit", "message") or "",
            )
        )

    reviews: list[Review] = []
    for raw in raw_reviews:
        state = raw.get("state")
        login = _text(raw, "user", "login")
        commit_id = raw.get("commit_id")
        if state not in REVIEW_STATES or login is None or not isinstance(commit_id, str):
            continue  # PENDING (unsubmitted) reviews and deleted users carry no decision
        review_id = raw.get("id")
        reviews.append(
            Review(
                evidence_id=f"github:review:{number}:{review_id}",
                source="github",
                source_ref=f"{url}/pull/{number}#pullrequestreview-{review_id}",
                retrieved_at=at,
                extractor_version=__version__,
                reviewer_login=login,
                state=state,
                commit_id=commit_id,
                submitted_at=_timestamp(_text(raw, "submitted_at"), f"review {review_id}"),
            )
        )

    work_items = keys_analysis.find_keys(
        branch=_text(pr, "head", "ref") or "",
        pr_title=_text(pr, "title") or "",
        pr_body=_text(pr, "body") or "",
        commits=raw_commits,
        pattern=policy.workitem_key_pattern,
        retrieved_at=at,
    )
    return _Change(
        ref=ChangeRef(
            repo=repo,
            pr_number=number,
            base_sha=base_sha,
            head_sha=head_sha,
            merge_base_sha=merge_base_sha,
        ),
        work_items=work_items,
        commits=commits,
        reviews=reviews,
        raw_commits=raw_commits,
    )


# ---- analysis stages (pull-request mode) ----------------------------------------------------


def _decode(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")


def _governance(
    change: ChangeRef, hunks: list[DiffHunk], git: GitSource, policy: Policy, at: datetime
) -> tuple[GovernanceSnapshot, list[GovernanceChange], list[str]]:
    """CODEOWNERS, ADRs and the policy file at the base commit; governance files in the diff."""
    notes: list[str] = []
    codeowners_path: str | None = None
    codeowners_text: str | None = None
    for path in CODEOWNERS_PATHS:
        codeowners_text = _decode(git.file_bytes(change.base_sha, path))
        if codeowners_text is not None:
            codeowners_path = path
            break
    if codeowners_text is None:
        notes.append(f"no CODEOWNERS at base {change.base_sha[:12]}")

    adr_files: dict[str, str] = {}
    for path in git.list_files(change.base_sha, ADR_FOLDER):
        text = _decode(git.file_bytes(change.base_sha, path))
        if text is not None and path.endswith(".md"):
            adr_files[path] = text

    policy_bytes = git.file_bytes(change.base_sha, POLICY_PATH)
    policy_blob_sha = None if policy_bytes is None else sha256_hex(policy_bytes)

    snapshot = governance_analysis.build_snapshot(
        base_sha=change.base_sha,
        codeowners_text=codeowners_text,
        adr_files=adr_files,
        policy_blob_sha=policy_blob_sha,
        retrieved_at=at,
        codeowners_path=codeowners_path,
    )
    changed_files = sorted({(h.file_path, h.change_type) for h in hunks})
    changes = governance_analysis.governance_changes_in_diff(
        changed_files=changed_files, policy=policy, retrieved_at=at
    )
    return snapshot, changes, notes


def _commits_by_file(commits: list[Commit], git: GitSource) -> dict[str, list[str]]:
    """File path -> SHAs of the PR commits that touched it (diff against each first parent)."""
    touched: dict[str, list[str]] = {}
    for commit in sorted(commits, key=lambda c: (c.committed_at, c.sha)):
        try:
            hunks = git.diff_hunks(f"{commit.sha}^", commit.sha)
        except (KeyError, ValueError):
            continue  # root commit, or a commit missing from the clone
        for path in sorted({h.file_path for h in hunks}):
            touched.setdefault(path, []).append(commit.sha)
    return touched


def _hunk_range(hunk: DiffHunk, lines: list[str]) -> tuple[int, int]:
    """Lines the hunk covers: base side for a deleted file, head side otherwise.

    Blank lines at either edge are dropped: a new function arrives with the blank lines that
    separate it from its neighbours, and those must not push the hunk outside the function.
    A pure deletion inside a kept file has no head lines, so it maps to the line where the
    removed text used to be.
    """
    if hunk.change_type == "deleted":
        start, count = hunk.old_start, hunk.old_lines
    else:
        start, count = hunk.new_start, hunk.new_lines
    start = max(start, 1)
    end = start + max(count, 1) - 1
    first, last = start, end
    while first < last and first <= len(lines) and not lines[first - 1].strip():
        first += 1
    while last > first and last <= len(lines) and not lines[last - 1].strip():
        last -= 1
    return first, last


def _split_by_entity(span: tuple[int, int], entities: list[CodeEntity]) -> list[tuple[int, int]]:
    """Split a hunk that adds several whole classes or functions into one range per entity.

    A hunk inside one function stays as it is; so does a hunk no entity fits in (it becomes an
    unresolved change, as before).
    """
    first, last = span
    inside = [
        e for e in entities if e.kind != "module" and first <= e.start_line and e.end_line <= last
    ]
    outermost = [
        e
        for e in inside
        if not any(
            o is not e and o.start_line <= e.start_line and e.end_line <= o.end_line for o in inside
        )
    ]
    if len(outermost) < 2:
        return [span]
    return sorted({(e.start_line, e.end_line) for e in outermost})


def _code(
    change: ChangeRef, hunks: list[DiffHunk], commits: list[Commit], git: GitSource, at: datetime
) -> tuple[list[CodeEntity], list[ChangedEntity], list[CodeEdge]]:
    """Entities of every Python file at head, the entity each hunk falls in, and imports.

    The whole repository is parsed (not just the changed files) so that semantic ranking picks
    the top candidates out of all the code; ranking only the changed files would put every
    changed function in the top five and C9 could never fire.
    """
    by_file: dict[str, list[DiffHunk]] = {}
    for hunk in hunks:
        if hunk.file_path.endswith(".py"):
            by_file.setdefault(hunk.file_path, []).append(hunk)
    touched = _commits_by_file(commits, git)
    all_shas = [c.sha for c in commits]

    entities: list[CodeEntity] = []
    changed: list[ChangedEntity] = []
    sources: dict[str, bytes] = {}
    for path in sorted(by_file):
        file_hunks = by_file[path]
        deleted = all(h.change_type == "deleted" for h in file_hunks)
        sha = change.merge_base_sha if deleted else change.head_sha
        source = git.file_bytes(sha, path)
        if source is None:
            continue
        sources[path] = source
        text_lines = source.decode("utf-8", errors="replace").splitlines()
        file_entities = code_analysis.extract_entities(file_path=path, source=source)
        entities += file_entities
        for change_type in sorted({h.change_type for h in file_hunks}):
            changed += code_analysis.map_hunks_to_entities(
                file_path=path,
                entities=file_entities,
                hunk_lines=[
                    part
                    for h in file_hunks
                    if h.change_type == change_type
                    for part in _split_by_entity(_hunk_range(h, text_lines), file_entities)
                ],
                change_type=change_type,
                commit_shas=touched.get(path, all_shas),
                retrieved_at=at,
            )
    for path in git.list_files(change.head_sha):
        if not path.endswith(".py") or path in sources:
            continue
        source = git.file_bytes(change.head_sha, path)
        if source is None:
            continue
        sources[path] = source
        entities += code_analysis.extract_entities(file_path=path, source=source)
    entities.sort(key=lambda e: (e.file, e.start_line, e.entity_id))
    edges = code_analysis.build_import_graph(entities=entities, file_sources=sources)
    return entities, changed, edges


def _latest_versions(versions: list[RequirementVersion]) -> list[RequirementVersion]:
    """The newest resolved version of each requirement (what the code should implement now)."""
    latest: dict[str, RequirementVersion] = {}
    for version in versions:
        if not version.content_resolved:
            continue
        current = latest.get(version.key)
        if current is None or version.version_no > current.version_no:
            latest[version.key] = version
    return [latest[key] for key in sorted(latest)]


def _criteria(versions: list[RequirementVersion]) -> list[Criterion]:
    out: list[Criterion] = []
    for version in _latest_versions(versions):
        text = "\n\n".join(t for t in (version.description, version.acceptance_criteria) if t)
        out += criteria_analysis.split_criteria(
            requirement_key=version.key, version_no=version.version_no, description=text
        )
    return out


def _people(
    commits: list[Commit], reviews: list[Review], identity: IdentityMap
) -> list[ResolvedPerson]:
    """Every commit author and reviewer, resolved to a Jira account id when the map knows them."""
    by_email = {email.lower(): p for p in identity.people for email in p.git_emails}
    by_login = {p.github_login.lower(): p for p in identity.people if p.github_login}
    seen: set[tuple[str | None, str | None]] = set()
    people: list[ResolvedPerson] = []
    pairs = [(c.author_email or None, c.author_login) for c in commits]
    pairs += [(None, r.reviewer_login) for r in reviews]
    for email, login in pairs:
        if (email, login) in seen:
            continue
        seen.add((email, login))
        entry = (by_email.get(email.lower()) if email else None) or (
            by_login.get(login.lower()) if login else None
        )
        people.append(
            ResolvedPerson(
                git_email=email,
                github_login=login,
                jira_account_id=entry.jira_account_id if entry else None,
            )
        )
    return sorted(people, key=lambda p: (p.github_login or "", p.git_email or ""))


# ---- collect --------------------------------------------------------------------------------


def collect_bundle(
    request: CollectRequest,
    source: JiraSource,
    log: EventLog,
    sources: ChangeSources | None = None,
) -> EvidenceBundle:
    """Run every collection stage; raises `CollectionError` (exit 3) if a source fails."""
    at = request.collected_at
    notes = list(request.notes)
    with log.stage("resolve_change"):
        if request.target is not None:
            if sources is None:
                raise ConfigError("a pull-request target needs GitHub and git sources")
            change = _from_pull_request(request.target, sources, request.policy, at)
        elif request.change is not None:
            change = _from_change_file(request.change, request.change_label, at)
            notes.append(NOTE_NOT_COLLECTED)
        else:
            raise ConfigError("give either a change file or a pull request to collect")

    events: list[ChangeEvent] = []
    versions: list[RequirementVersion] = []
    lifecycle: list[LifecycleFacts] = []
    keys = sorted({item.key for item in change.work_items})
    if request.target is not None and not keys:
        notes.append("no work-item key found in the branch, PR title, PR body or commits")
    with log.stage("collect_requirements"):
        approval = request.policy.approval
        authority = request.policy.priority_authority
        approvers = _members(source, approval.approver_group, approval.approver_account_ids, notes)
        authorities = _members(source, authority.group, authority.account_ids, notes)
        categories: dict[str, dict[str, str]] = {}
        for key in keys:
            try:
                issue = source.fetch_issue(key)
            except CollectionError as error:
                if error.status != 404:
                    raise
                notes.append(f"work item {key} not found")
                continue
            project = key.split("-")[0]
            if project not in categories:
                categories[project] = source.fetch_status_categories(project)
            if approvers is None:
                notes.append(f"{NOTE_APPROVERS_UNKNOWN}{key}")
            history = JiraHistory(issue=issue, changelog=source.fetch_changelog(key))
            context = LifecycleContext(
                policy=request.policy,
                approvers=approvers or frozenset(),
                authorities=authorities,
                new_statuses=frozenset(
                    name for name, kind in categories[project].items() if kind == "new"
                ),
                retrieved_at=at,
                extractor_version=__version__,
            )
            with log.stage("lifecycle"):
                result = reconstruct(history, context, TEXT_FORMS)
            events += result.events
            versions += result.versions
            lifecycle.append(result.facts)

    extractor_versions = {"jira": __version__, "lifecycle": __version__}
    governance = GovernanceSnapshot(
        evidence_id=f"git:governance:{change.ref.base_sha}",
        source="git",
        source_ref=f"change-file:{request.change_label}@{change.ref.base_sha}",
        retrieved_at=at,
        extractor_version=__version__,
        base_sha=change.ref.base_sha,
        codeowners_path=None,
        owner_rules=[],
        adrs=[],
        policy_blob_sha=None,
    )
    governance_changes: list[GovernanceChange] = []
    entities: list[CodeEntity] = []
    changed: list[ChangedEntity] = []
    edges: list[CodeEdge] = []
    criteria: list[Criterion] = []
    candidates: list[SemanticCandidate] = []
    model_ids: list[str] = []
    people: list[ResolvedPerson] = []

    if request.target is not None and sources is not None:
        extractor_versions |= {
            "github": __version__,
            "keys": keys_analysis.EXTRACTOR_VERSION,
            "governance": governance_analysis.EXTRACTOR_VERSION,
            "code": code_analysis.EXTRACTOR_VERSION,
            "criteria": criteria_analysis.EXTRACTOR_VERSION,
            "semantic": semantic_analysis.EXTRACTOR_VERSION,
        }
        with log.stage("diff"):
            hunks = sources.git.diff_hunks(
                change.ref.merge_base_sha, change.ref.head_sha, context_lines=0
            )
        with log.stage("governance"):
            governance, governance_changes, governance_notes = _governance(
                change.ref, hunks, sources.git, request.policy, at
            )
            notes += governance_notes
        with log.stage("code"):
            entities, changed, edges = _code(change.ref, hunks, change.commits, sources.git, at)
        with log.stage("criteria"):
            criteria = _criteria(versions)
            if keys and not criteria:
                notes.append("no acceptance criteria found in the linked requirements")
        with log.stage("semantic"):
            ranked = semantic_analysis.rank_candidates(
                criteria=criteria,
                entities=entities,
                top_k=request.policy.semantic.top_k,
                model_name=request.embedding_model or semantic_analysis.DEFAULT_MODEL,
                bm25_only=request.bm25_only,
            )
            candidates = ranked.candidates
            if candidates:
                model_ids = [ranked.model_id]
        people = _people(change.commits, change.reviews, request.identity_map)

    with log.stage("freeze"):
        return EvidenceBundle(
            schema_version="0",
            change=change.ref,
            codeatlas_version=__version__,
            collected_at=at,
            run_id=request.run_id,
            extractor_versions=extractor_versions,
            model_ids=model_ids,
            model_file_hashes={},
            policy=request.policy,
            effective_policy_hash=effective_policy_hash(request.policy),
            identity_map=request.identity_map,
            work_items=change.work_items,
            change_events=events,
            versions=versions,
            lifecycle=lifecycle,
            commits=change.commits,
            reviews=change.reviews,
            people=people,
            governance=governance,
            governance_changes=governance_changes,
            entities=entities,
            changed=changed,
            edges=edges,
            criteria=criteria,
            candidates=candidates,
            collection_notes=sorted(set(notes)),
        )


def collect_and_evaluate(
    request: CollectRequest,
    source: JiraSource,
    store: BundleStore,
    log: EventLog,
    sources: ChangeSources | None = None,
) -> tuple[str, Verdict]:
    """Collect, freeze, store, evaluate. Nothing is stored unless collection completed."""
    bundle = collect_bundle(request, source, log, sources)
    with log.stage("store"):
        bundle_hash = store.save(bundle)
        # Saving sorts every list and trims timestamps and scores. Replay judges the saved file,
        # so collect judges it too; the in-memory bundle could give a different verdict.
        frozen = load_bundle(store.folder(bundle_hash))
    with log.stage("evaluate"):
        verdict = evaluate_bundle(frozen, bundle_hash)
    store.save_verdict(verdict)
    return bundle_hash, verdict


def now_utc() -> datetime:
    """Collection time, to the second (the only clock read in a run)."""
    return datetime.now(UTC).replace(microsecond=0)
