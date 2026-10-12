"""View models: turn a stored case into exactly what each page template shows.

Pure functions over schema objects. Templates only loop and print; every decision (grouping,
ordering, positions, labels) is made here so it can be unit-tested.
"""

import difflib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from codeatlas.api.cases import Case, ReplayOutcome
from codeatlas.schema import (
    CheckResult,
    EvidenceBundle,
    LifecycleFacts,
    RequirementVersion,
    TraceLink,
)

CASE_NAMES: dict[str, str] = {
    "C1": "Requirement unchanged after coding began",
    "C2": "PR leaves its own rules alone",
    "C3": "Requirement approved, and the approval is current",
    "C4": "Assignee wrote the code",
    "C5": "Code owner approved the head commit",
    "C6": "Code linked to the current requirement version",
    "C7": "Changed behaviour has test evidence",
    "C8": "Priority changed only by someone with authority",
    "C9": "Every changed function is explained by the requirement",
}

VERDICT_RULES = (
    "Blocking violation → FAIL",
    "Review violation → REVIEW",
    "Required evidence missing → UNKNOWN",
    "Otherwise → PASS",
)

LINK_LABELS = {
    "OBSERVED": "Observed",
    "DETERMINISTICALLY_DERIVED": "Derived",
    "SEMANTIC_CANDIDATE": "Candidate",
    "CONFLICTING": "Conflicting",
    "UNRESOLVED": "Unresolved",
}


def fmt_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def short(sha: str | None, n: int = 7) -> str:
    return sha[:n] if sha else "—"


def _safe_url(ref: str) -> str | None:
    """Only http(s) links are rendered as links; anything else stays plain text."""
    return ref if ref.startswith(("https://", "http://")) else None


# --- evidence ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceChip:
    evidence_id: str
    kind: str  # Jira, Commit, Review, File, Code, Key, Ref
    ref: str
    note: str
    href: str | None


def _jira_ref(evidence_id: str, key: str) -> str:
    """`jira:SBX-4:history:10046:priority` -> `SBX-4 #10046`."""
    parts = evidence_id.split(":")
    return f"{parts[1]} #{parts[3]}" if len(parts) >= 4 else key


def evidence_index(bundle: EvidenceBundle) -> dict[str, EvidenceChip]:
    index: dict[str, EvidenceChip] = {}

    def add(eid: str, kind: str, ref: str, note: str, source_ref: str) -> None:
        index[eid] = EvidenceChip(eid, kind, ref, note, _safe_url(source_ref))

    for w in bundle.work_items:
        add(w.evidence_id, "Key", w.key, f"found in {w.found_in.replace('_', ' ')}", w.source_ref)
    for e in bundle.change_events:
        note = f"{e.field} changed, {fmt_time(e.at)}"
        add(e.evidence_id, "Jira", f"{e.key} #{e.history_id}", note, e.source_ref)
    for c in bundle.commits:
        first = c.message.splitlines()[0] if c.message else ""
        add(
            c.evidence_id,
            "Commit",
            short(c.sha),
            f"{c.author_login or c.author_email}: {first}",
            c.source_ref,
        )
    for r in bundle.reviews:
        note = f"{r.reviewer_login} {r.state.lower()} at {short(r.commit_id)}"
        add(r.evidence_id, "Review", r.evidence_id.rsplit(":", 1)[-1], note, r.source_ref)
    g = bundle.governance
    add(g.evidence_id, "File", "governance", f"rules at base {short(g.base_sha)}", g.source_ref)
    for gc in bundle.governance_changes:
        add(gc.evidence_id, "File", gc.path, gc.change_type, gc.source_ref)
    for facts in bundle.lifecycle:
        for a in facts.approvals:
            if a.evidence_id not in index:
                who = a.actor_account_id or "Jira automation"
                add(
                    a.evidence_id,
                    "Jira",
                    f"{a.key} #{a.history_id}",
                    f"approved by {who}, {fmt_time(a.at)}",
                    "",
                )
        for p in facts.priority_changes:
            if p.evidence_id not in index:
                who = p.actor_account_id or "Jira automation"
                add(
                    p.evidence_id,
                    "Jira",
                    _jira_ref(p.evidence_id, p.key),
                    f"priority {p.from_priority} → {p.to_priority} by {who}, {fmt_time(p.at)}",
                    "",
                )
    for ch in bundle.changed:
        label = ch.entity_id or f"{ch.file}:{ch.hunk_lines[0]}"
        add(
            ch.evidence_id,
            "Code",
            label,
            f"{ch.change}, lines {ch.hunk_lines[0]}–{ch.hunk_lines[1]}",
            ch.source_ref,
        )
    return index


def chips(index: dict[str, EvidenceChip], ids: list[str]) -> list[EvidenceChip]:
    return [index.get(i) or EvidenceChip(i, "Ref", i, "not in this bundle", None) for i in ids]


# --- case detail ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckView:
    case: str
    name: str
    group: str  # violated | unknown | satisfied | not_checked
    tone: str  # fail | review | unknown | pass | muted
    outcome_label: str
    severity_label: str
    message: str
    evidence: list[EvidenceChip]


def _check_view(result: CheckResult, index: dict[str, EvidenceChip]) -> CheckView:
    if result.outcome == "VIOLATED":
        group, tone, label = (
            "violated",
            ("fail" if result.severity == "block" else "review"),
            "Violated",
        )
    elif result.outcome == "INSUFFICIENT_EVIDENCE" and result.required:
        group, tone, label = "unknown", "unknown", "Not enough evidence"
    elif result.outcome == "SATISFIED":
        group, tone, label = "satisfied", "pass", "Satisfied"
    elif result.outcome == "NOT_APPLICABLE":
        group, tone, label = "not_checked", "muted", "Not applicable"
    else:
        group, tone, label = "not_checked", "muted", "Not enough evidence (optional)"
    severity = {"block": "Blocking check", "review": "Review check", "none": "Informational"}
    return CheckView(
        case=result.case,
        name=CASE_NAMES.get(result.case, result.case),
        group=group,
        tone=tone,
        outcome_label=label,
        severity_label=severity[result.severity],
        message=result.message,
        evidence=chips(index, result.evidence_ids),
    )


_GROUP_ORDER = {"violated": 0, "unknown": 1, "satisfied": 2, "not_checked": 3}
_TONE_ORDER = {"fail": 0, "review": 1, "unknown": 2, "pass": 3, "muted": 4}


@dataclass(frozen=True)
class CaseDetailView:
    checks: list[CheckView]
    counts: dict[str, int]
    rule_applied: int  # 1-4, row of the verdict truth table that decided
    reason: str
    evidence_counts: dict[str, int]
    requirements: list[tuple[str, int, bool]]  # key, versions, latest version approved
    notes: list[str]


def _rule_applied(results: list[CheckResult]) -> int:
    violated = [r for r in results if r.outcome == "VIOLATED"]
    if any(r.severity == "block" for r in violated):
        return 1
    if any(r.severity == "review" for r in violated):
        return 2
    if any(r.required and r.outcome == "INSUFFICIENT_EVIDENCE" for r in results):
        return 3
    return 4


def _reason(rule: int, checks: list[CheckView]) -> str:
    def cases(tone: str) -> str:
        return ", ".join(c.case for c in checks if c.tone == tone)

    if rule == 1:
        return f"{cases('fail')}: a blocking check was violated."
    if rule == 2:
        return f"{cases('review')}: a review check was violated; a person should look."
    if rule == 3:
        return f"{cases('unknown')}: required evidence is missing, so no PASS is given."
    return "Every required check was satisfied."


def _latest_approved(bundle: EvidenceBundle, key: str) -> bool:
    versions = sorted((v for v in bundle.versions if v.key == key), key=lambda v: v.version_no)
    facts = _facts(bundle, key)
    if not versions or facts is None:
        return False
    return bool(_approvals_in(facts, versions[-1]))


def case_detail(case: Case) -> CaseDetailView:
    bundle, verdict = case.bundle, case.verdict
    index = evidence_index(bundle)
    checks = [_check_view(r, index) for r in verdict.results]
    ran = {r.case for r in verdict.results}
    for missing in sorted(set(CASE_NAMES) - ran):
        checks.append(
            CheckView(
                case=missing,
                name=CASE_NAMES[missing],
                group="not_checked",
                tone="muted",
                outcome_label="Not run",
                severity_label="Not in this ruleset",
                message=f"No rule for {missing} ran (ruleset {verdict.ruleset_version}).",
                evidence=[],
            )
        )
    checks.sort(key=lambda c: (_GROUP_ORDER[c.group], _TONE_ORDER[c.tone], c.case))
    rule = _rule_applied(verdict.results)
    counts = Counter(c.group for c in checks)
    return CaseDetailView(
        checks=checks,
        counts={"all": len(checks), **{g: counts.get(g, 0) for g in _GROUP_ORDER}},
        rule_applied=rule,
        reason=_reason(rule, checks),
        evidence_counts={
            "Jira changes": len(bundle.change_events),
            "Commits": len(bundle.commits),
            "Reviews": len(bundle.reviews),
            "Changed functions": len([c for c in bundle.changed if c.entity_id]),
        },
        requirements=[
            (key, len([v for v in bundle.versions if v.key == key]), _latest_approved(bundle, key))
            for key in case.summary.keys
        ],
        notes=list(bundle.collection_notes),
    )


# --- people --------------------------------------------------------------------------------


def _display_name(bundle: EvidenceBundle, account_id: str | None) -> str:
    if account_id is None:
        return "Jira automation"
    for entry in bundle.identity_map.people:
        if entry.jira_account_id == account_id and entry.github_login:
            return entry.github_login
    return account_id


def _resolve_author(bundle: EvidenceBundle, email: str, login: str | None) -> str | None:
    """Jira account id of a commit author, or None when the identity map does not know them."""
    for person in bundle.people:
        if person.jira_account_id and (
            (person.git_email and person.git_email == email)
            or (login and person.github_login == login)
        ):
            return person.jira_account_id
    for entry in bundle.identity_map.people:
        if email in entry.git_emails or (login and entry.github_login == login):
            return entry.jira_account_id
    return None


# --- requirement timeline + lifecycle ------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    index: int
    at: str
    at_short: str  # "10-04 04:00" for the label on the line
    kind: str  # edit, status, approval, priority, assignee, commit, review
    kind_label: str
    title: str
    detail: str
    evidence_id: str | None
    problem: bool
    above: bool  # label above the line (alternates so labels never overlap)


@dataclass(frozen=True)
class VersionBand:
    version_no: int
    label: str
    approved: bool
    col_start: int  # 1-based CSS grid lines
    col_end: int


@dataclass(frozen=True)
class LifecycleRow:
    version_no: int
    window: str
    current: bool
    requester: str
    approved_by: list[str]
    priority: str
    priority_by: str
    assignee: str
    implementers: list[tuple[str, bool]]  # name, resolved


@dataclass(frozen=True)
class DiffLine:
    tag: str  # same | removed | added
    text: str


@dataclass(frozen=True)
class RequirementView:
    key: str
    events: list[TimelineEvent]
    bands: list[VersionBand]
    rows: list[LifecycleRow]
    diff: list[DiffLine]
    diff_title: str
    criteria_now: list[str]
    flagged_cases: dict[str, str]  # case -> tone, for C3/C4 tags in the table
    unresolved_note: str | None


def _facts(bundle: EvidenceBundle, key: str) -> LifecycleFacts | None:
    return next((f for f in bundle.lifecycle if f.key == key), None)


def _in_window(at: datetime, version: RequirementVersion) -> bool:
    return at >= version.valid_from and (version.valid_to is None or at < version.valid_to)


def _approvals_in(facts: LifecycleFacts, version: RequirementVersion) -> list[str]:
    return [
        a.actor_account_id or "Jira automation"
        for a in facts.approvals
        if a.revoked_at is None and _in_window(a.at, version)
    ]


def requirement_view(case: Case, key: str) -> RequirementView:
    bundle, verdict = case.bundle, case.verdict
    versions = sorted((v for v in bundle.versions if v.key == key), key=lambda v: v.version_no)
    facts = _facts(bundle, key)
    flagged = {r.case: r for r in verdict.results if r.outcome == "VIOLATED"}
    problem_ids = {i for r in flagged.values() for i in r.evidence_ids}

    raw: list[tuple[datetime, int, str, str, str, str, str | None]] = []
    approval_ids = {a.evidence_id for a in facts.approvals} if facts else set()
    priority_ids = {p.evidence_id for p in facts.priority_changes} if facts else set()
    fields = set(bundle.policy.requirement_fields)
    for e in bundle.change_events:
        if e.key != key or e.evidence_id in approval_ids | priority_ids:
            continue
        who = _display_name(bundle, e.actor_account_id)
        if e.field in fields:
            raw.append(
                (
                    e.at,
                    1,
                    "edit",
                    f"{e.field.replace('_', ' ').capitalize()} edited",
                    f"{who} changed the {e.field.replace('_', ' ')}.",
                    "Edit",
                    e.evidence_id,
                )
            )
        elif e.field == "status":
            raw.append(
                (
                    e.at,
                    1,
                    "status",
                    f"Status → {e.to_value}",
                    f"{who} moved {key} from {e.from_value} to {e.to_value}.",
                    "Jira",
                    e.evidence_id,
                )
            )
    if facts:
        for a in facts.approvals:
            who = _display_name(bundle, a.actor_account_id)
            group = "an approver" if a.by_approver_group else "someone outside the approver group"
            detail = f"{who} ({group}) approved {key}."
            if a.revoked_at:
                detail += f" Revoked {fmt_time(a.revoked_at)}."
            raw.append((a.at, 2, "approval", "Approved", detail, "Approval", a.evidence_id))
        for p in facts.priority_changes:
            who = _display_name(bundle, p.actor_account_id)
            authority = {True: "has authority", False: "has no authority", None: "system"}
            raw.append(
                (
                    p.at,
                    3,
                    "priority",
                    f"Priority → {p.to_priority}",
                    f"{who} ({authority[p.actor_has_authority]}) changed priority from "
                    f"{p.from_priority} to {p.to_priority}.",
                    "Priority",
                    p.evidence_id,
                )
            )
        for at, account in facts.assignee_history:
            name = _display_name(bundle, account) if account else "nobody"
            raw.append(
                (
                    at,
                    0,
                    "assignee",
                    f"Assigned to {name}",
                    f"Assignee set to {name}.",
                    "Assignee",
                    None,
                )
            )
    for c in bundle.commits:
        first = c.message.splitlines()[0] if c.message else ""
        raw.append(
            (
                c.committed_at,
                4,
                "commit",
                f"Commit {short(c.sha)}",
                f"{c.author_login or c.author_email}: {first}",
                "Commit",
                c.evidence_id,
            )
        )
    for r in bundle.reviews:
        raw.append(
            (
                r.submitted_at,
                5,
                "review",
                f"Review: {r.state.lower()}",
                f"{r.reviewer_login} {r.state.lower()} commit {short(r.commit_id)}.",
                "Review",
                r.evidence_id,
            )
        )
    raw.sort(key=lambda item: (item[0], item[1], item[3]))

    events = [
        TimelineEvent(
            index=i,
            at=fmt_time(at),
            at_short=at.strftime("%m-%d %H:%M"),
            kind=kind,
            kind_label=label,
            title=title,
            detail=detail,
            evidence_id=eid,
            problem=eid in problem_ids if eid else False,
            above=i % 2 == 0,
        )
        for i, (at, _, kind, title, detail, label, eid) in enumerate(raw)
    ]

    def version_of(at: datetime) -> RequirementVersion | None:
        return next((v for v in versions if _in_window(at, v)), None)

    bands: list[VersionBand] = []
    for i, item in enumerate(raw):
        v = version_of(item[0])
        if v is None:
            continue
        if bands and bands[-1].version_no == v.version_no and bands[-1].col_end == i + 1:
            last = bands.pop()
            bands.append(
                VersionBand(last.version_no, last.label, last.approved, last.col_start, i + 2)
            )
        else:
            approved = bool(facts and _approvals_in(facts, v))
            label = f"Version {v.version_no} · {'approved' if approved else 'not approved'}"
            bands.append(VersionBand(v.version_no, label, approved, i + 1, i + 2))

    rows: list[LifecycleRow] = []
    for v in versions:
        approvers = [_display_name(bundle, a) for a in _approvals_in(facts, v)] if facts else []
        setter = "—"
        if facts:
            setters = [
                p
                for p in facts.priority_changes
                if p.to_priority == v.priority and (v.valid_to is None or p.at < v.valid_to)
            ]
            if setters:
                newest = max(setters, key=lambda p: p.at)
                setter = _display_name(bundle, newest.actor_account_id)
        implementers: dict[str, bool] = {}
        for c in bundle.commits:
            if not _in_window(c.committed_at, v):
                continue
            account = _resolve_author(bundle, c.author_email, c.author_login)
            if account:
                implementers[_display_name(bundle, account)] = True
            else:
                implementers[c.author_login or c.author_email] = False
        until = fmt_time(v.valid_to) if v.valid_to else "now"
        rows.append(
            LifecycleRow(
                version_no=v.version_no,
                window=f"{fmt_time(v.valid_from)} – {until}",
                current=v.valid_to is None,
                requester=_display_name(bundle, facts.requester_account_id) if facts else "—",
                approved_by=approvers,
                priority=v.priority or "—",
                priority_by=setter,
                assignee=_display_name(bundle, v.assignee_account_id)
                if v.assignee_account_id
                else "—",
                implementers=sorted(implementers.items()),
            )
        )

    diff: list[DiffLine] = []
    diff_title = ""
    criteria_now: list[str] = []
    if versions:
        latest = versions[-1]
        criteria_now = (latest.acceptance_criteria or "").splitlines()
        if len(versions) > 1:
            prev = versions[-2]
            diff_title = f"v{prev.version_no} → v{latest.version_no}"
            old = (prev.acceptance_criteria or "").splitlines()
            for line in difflib.ndiff(old, criteria_now):
                tag = {" ": "same", "-": "removed", "+": "added"}.get(line[:1])
                if tag:
                    diff.append(DiffLine(tag, line[2:]))
    unresolved = sorted({f for v in versions for f in v.unresolved_fields})
    note = (
        f"Some text could not be rebuilt from Jira history ({', '.join(unresolved)}); "
        "it is shown as unresolved, never guessed."
        if unresolved
        else None
    )
    return RequirementView(
        key=key,
        events=events,
        bands=bands,
        rows=rows,
        diff=diff,
        diff_title=diff_title,
        criteria_now=criteria_now,
        flagged_cases={
            c: ("fail" if r.severity == "block" else "review") for c, r in flagged.items()
        },
        unresolved_note=note,
    )


# --- trace links ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    label: str
    sub: str
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class GraphEdge:
    path: str
    state: str
    title: str


@dataclass(frozen=True)
class CandidateRow:
    rank: int
    entity: str
    location: str
    bm25: str
    dense: str
    score: str
    bar: int  # percent of the best score in this list
    state: str
    state_label: str


@dataclass(frozen=True)
class CriterionView:
    criterion_id: str
    text: str
    rows: list[CandidateRow]


@dataclass(frozen=True)
class LinkRow:
    key: str
    entity: str
    state: str
    state_label: str
    reasons: list[str]
    evidence: list[EvidenceChip]


@dataclass(frozen=True)
class LinksView:
    width: int
    height: int
    left: list[GraphNode]
    right: list[GraphNode]
    edges: list[GraphEdge]
    criteria: list[CriterionView]
    links: list[LinkRow]
    c9: CheckView | None
    model_ids: list[str]
    top_k: int
    state_counts: dict[str, int] = field(default_factory=dict)


def _versions_label(bundle: EvidenceBundle, key: str) -> str:
    n = len([v for v in bundle.versions if v.key == key])
    return f"{n} version" + ("" if n == 1 else "s")


def _entity_label(entity_id: str) -> str:
    parts = entity_id.split(".")
    return ".".join(parts[-2:]) + "()" if len(parts) > 1 else entity_id


def links_view(case: Case, links: list[TraceLink]) -> LinksView:
    bundle = case.bundle
    index = evidence_index(bundle)
    entities = {e.entity_id: e for e in bundle.entities}
    changed_ids = sorted({c.entity_id for c in bundle.changed if c.entity_id})
    keys = case.summary.keys

    width, node_h, gap, top = 900, 52, 20, 16
    lx, lw, rw = 16, 220, 300
    rx = width - 16 - rw

    def ys(n: int) -> list[int]:
        return [top + i * (node_h + gap) for i in range(n)]

    height = max(1, len(keys), len(changed_ids)) * (node_h + gap) + top
    left = [
        GraphNode(k, k, _versions_label(bundle, k), lx, y, lw, node_h)
        for k, y in zip(keys, ys(len(keys)), strict=True)
    ]

    def location(entity_id: str) -> str:
        e = entities.get(entity_id)
        return f"{e.file}:{e.start_line}–{e.end_line}" if e else ""

    right = [
        GraphNode(e, _entity_label(e), location(e), rx, y, rw, node_h)
        for e, y in zip(changed_ids, ys(len(changed_ids)), strict=True)
    ]
    lpos = {n.node_id: n for n in left}
    rpos = {n.node_id: n for n in right}
    mid = (lx + lw + rx) // 2
    edges = []
    for link in links:
        a, b = lpos.get(link.requirement_key), rpos.get(link.entity_id)
        if a is None or b is None:
            continue
        y1, y2 = a.y + a.h // 2, b.y + b.h // 2
        edges.append(
            GraphEdge(
                path=f"M{a.x + a.w} {y1} C{mid} {y1} {mid} {y2} {b.x} {y2}",
                state=link.state,
                title=f"{link.requirement_key} → {link.entity_id}: {LINK_LABELS[link.state]}",
            )
        )

    state_of = {(link.requirement_key, link.entity_id): link.state for link in links}
    top_k = bundle.policy.semantic.top_k
    criteria: list[CriterionView] = []
    for crit in sorted(bundle.criteria, key=lambda c: c.criterion_id):
        if crit.key not in keys:
            continue
        cands = sorted(
            (c for c in bundle.candidates if c.criterion_id == crit.criterion_id),
            key=lambda c: c.fused_rank,
        )[:top_k]
        best = max((c.fused_score for c in cands), default=0.0) or 1.0
        rows = []
        for c in cands:
            state = state_of.get((crit.key, c.entity_id), "UNRESOLVED")
            rows.append(
                CandidateRow(
                    rank=c.fused_rank,
                    entity=_entity_label(c.entity_id),
                    location=location(c.entity_id),
                    bm25=f"#{c.bm25_rank}" if c.bm25_rank else "—",
                    dense=f"#{c.dense_rank}" if c.dense_rank else "—",
                    score=f"{c.fused_score:.4f}",
                    bar=max(4, round(100 * c.fused_score / best)),
                    state=state,
                    state_label=LINK_LABELS[state],
                )
            )
        criteria.append(CriterionView(crit.criterion_id, crit.text, rows))

    c9 = next((r for r in case.verdict.results if r.case == "C9"), None)
    return LinksView(
        width=width,
        height=height,
        left=left,
        right=right,
        edges=edges,
        criteria=criteria,
        links=[
            LinkRow(
                key=link.requirement_key,
                entity=link.entity_id,
                state=link.state,
                state_label=LINK_LABELS[link.state],
                reasons=link.reasons,
                evidence=chips(index, link.evidence_ids),
            )
            for link in links
        ],
        c9=_check_view(c9, index) if c9 else None,
        model_ids=list(bundle.model_ids),
        top_k=top_k,
        state_counts=dict(Counter(link.state for link in links)),
    )


# --- replay --------------------------------------------------------------------------------


@dataclass(frozen=True)
class HashGroup:
    text: str
    match: bool


def hash_groups(value: str | None, other: str | None) -> list[HashGroup]:
    if not value:
        return []
    groups = [value[i : i + 8] for i in range(0, len(value), 8)]
    others = [other[i : i + 8] for i in range(0, len(other), 8)] if other else []
    return [HashGroup(g, i < len(others) and others[i] == g) for i, g in enumerate(groups)]


@dataclass(frozen=True)
class ReplayView:
    outcome: ReplayOutcome
    stored: list[HashGroup]
    recomputed: list[HashGroup]


def replay_view(outcome: ReplayOutcome) -> ReplayView:
    return ReplayView(
        outcome=outcome,
        stored=hash_groups(outcome.stored_verdict_hash, outcome.recomputed_verdict_hash),
        recomputed=hash_groups(outcome.recomputed_verdict_hash, outcome.stored_verdict_hash),
    )
