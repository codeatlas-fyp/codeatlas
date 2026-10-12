"""Requirement lifecycle from a Jira issue and its changelog (step-6 spec; spec §5 stage 5).

Pure: no I/O, no clock. Jira records history per field, so versions are rebuilt by walking the
changelog backwards from the current values, and every step is checked (the "chain check"):
where a field's history does not chain, its text is UNRESOLVED instead of guessed, and the change
events are always kept.

Rich text arrives in two formats (current value ADF, changelog wiki markup); `TextForms` is
injected by the pipeline because `analyze` may not import `collect` (contract, spec §8).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NamedTuple

from codeatlas.schema import (
    Approval,
    ChangeEvent,
    LifecycleFacts,
    Policy,
    PriorityChange,
    RequirementField,
    RequirementVersion,
)

_VERSION_FIELDS: tuple[RequirementField, ...] = ("summary", "description", "acceptance_criteria")
_USER_FIELDS = {"assignee", "reporter"}
_LIST_MARKER = re.compile(r"^(?:[-*#]+|\d+[.)])\s+")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class JiraHistory:
    """Raw collector output for one issue."""

    issue: dict[str, Any]
    changelog: list[dict[str, Any]]


@dataclass(frozen=True)
class LifecycleContext:
    policy: Policy
    approvers: frozenset[str]
    authorities: frozenset[str] | None  # None = unknown (e.g. group not readable)
    new_statuses: frozenset[str]  # names of statuses whose category is "new"
    retrieved_at: datetime
    extractor_version: str


@dataclass(frozen=True)
class TextForms:
    current: Callable[[Any], str]  # current field value (ADF) to plain text
    history: Callable[[str | None], str]  # changelog string (wiki markup) to plain text


@dataclass(frozen=True)
class Lifecycle:
    events: list[ChangeEvent]
    versions: list[RequirementVersion]
    facts: LifecycleFacts


class _Item(NamedTuple):
    history_id: str
    at: datetime
    actor: str | None
    field: str
    from_id: str | None
    from_string: str | None
    to_id: str | None
    to_string: str | None
    evidence_id: str


def parse_time(value: str) -> datetime:
    """Jira timestamp (`2026-10-07T16:27:48.950+0500`) as UTC, truncated to the second."""
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)


def comparison_form(text: str | None) -> str:
    """Text reduced for the chain check only: trimmed lines, single spaces, no list markers."""
    lines = []
    for line in (text or "").splitlines():
        line = _LIST_MARKER.sub("", _SPACES.sub(" ", line).strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


def reconstruct(source: JiraHistory, context: LifecycleContext, text: TextForms) -> Lifecycle:
    issue = source.issue
    fields: dict[str, Any] = issue.get("fields") or {}
    key = str(issue["key"])
    rich = {name for name, value in fields.items() if _is_adf(value)}
    rich.add("description")
    items = _items(key, source.changelog)
    events = [_event(key, _site(issue), item, rich, text, context) for item in items]
    versions = _versions(key, fields, items, rich, text, context.policy)
    facts = _facts(key, fields, items, context)
    return Lifecycle(events=events, versions=versions, facts=facts)


# --- changelog items and events ---------------------------------------------------------------


def _items(key: str, changelog: list[dict[str, Any]]) -> list[_Item]:
    flat = []
    for history in changelog:
        history_id = str(history["id"])
        at = parse_time(history["created"])
        actor = (history.get("author") or {}).get("accountId")
        for position, raw in enumerate(history.get("items") or []):
            field = str(raw.get("fieldId") or raw.get("field"))
            flat.append((at, _order(history_id), position, history_id, actor, field, raw))
    flat.sort(key=lambda entry: entry[:3])
    seen: dict[str, int] = {}
    items = []
    for at, _, _, history_id, actor, field, raw in flat:
        base = f"jira:{key}:history:{history_id}:{field}"
        seen[base] = seen.get(base, 0) + 1
        evidence_id = base if seen[base] == 1 else f"{base}:{seen[base]}"
        items.append(
            _Item(
                history_id,
                at,
                actor,
                field,
                raw.get("from"),
                raw.get("fromString"),
                raw.get("to"),
                raw.get("toString"),
                evidence_id,
            )
        )
    return items


def _is_adf(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "doc"


def _order(history_id: str) -> tuple[int, str]:
    return (int(history_id), "") if history_id.isdigit() else (0, history_id)


def _site(issue: dict[str, Any]) -> str:
    self_url = str(issue.get("self") or "")
    return self_url.split("/rest/")[0] if "/rest/" in self_url else "jira"


def _values(item: _Item, rich: set[str], text: TextForms) -> tuple[str | None, str | None]:
    if item.field in rich:
        return _history_text(item.from_string, text), _history_text(item.to_string, text)
    if item.field in _USER_FIELDS:
        return item.from_id, item.to_id
    return item.from_string, item.to_string


def _event(
    key: str, site: str, item: _Item, rich: set[str], text: TextForms, context: LifecycleContext
) -> ChangeEvent:
    from_value, to_value = _values(item, rich, text)
    return ChangeEvent(
        evidence_id=item.evidence_id,
        source="jira",
        source_ref=f"{site}/browse/{key}",
        retrieved_at=context.retrieved_at,
        extractor_version=context.extractor_version,
        key=key,
        history_id=item.history_id,
        field=item.field,
        from_value=from_value,
        to_value=to_value,
        actor_account_id=item.actor,
        at=item.at,
    )


# --- versions and the chain check -------------------------------------------------------------


def _history_text(value: str | None, text: TextForms) -> str | None:
    return text.history(value) or None if value is not None else None


def _current_text(value: Any, field: str, rich: set[str], text: TextForms) -> str | None:
    if value is None:
        return None
    if field in rich:
        return text.current(value) or None
    return str(value)


def _versions(
    key: str,
    fields: dict[str, Any],
    items: list[_Item],
    rich: set[str],
    text: TextForms,
    policy: Policy,
) -> list[RequirementVersion]:
    tracked: list[str] = [f for f in policy.requirement_fields if f in _VERSION_FIELDS]
    current: dict[str, str | None] = {
        f: _current_text(fields.get(f), f, rich, text) for f in tracked
    }
    changes = [i for i in items if i.field in tracked]
    starts: list[str] = []
    for item in changes:
        if item.history_id not in starts:
            starts.append(item.history_id)
    number_of = {history_id: n for n, history_id in enumerate(starts, start=2)}

    # Walk backwards: the state after the newest change is the current value.
    states = [dict(current)]
    for history_id in reversed(starts):
        before = dict(states[-1])
        for item in changes:
            if item.history_id == history_id:
                before[item.field] = _history_text(item.from_string, text)
        states.append(before)
    states.reverse()

    unresolved = _chain_check(changes, current, number_of, len(starts) + 1, text)
    created = parse_time(fields["created"])
    valid_from = [created] + [next(i.at for i in changes if i.history_id == h) for h in starts]
    versions = []
    for index, state in enumerate(states):
        number = index + 1
        broken = {f: reason for f, (limit, reason) in unresolved.items() if number < limit}
        content = {f: (None if f in broken else state.get(f)) for f in _VERSION_FIELDS}
        start = valid_from[index]
        versions.append(
            RequirementVersion(
                key=key,
                version_no=number,
                valid_from=start,
                valid_to=valid_from[index + 1] if index + 1 < len(valid_from) else None,
                summary=content["summary"],
                description=content["description"],
                acceptance_criteria=content["acceptance_criteria"],
                status=_in_force(items, "status", start, _name(fields.get("status"))) or "",
                priority=_in_force(items, "priority", start, _name(fields.get("priority"))),
                assignee_account_id=_in_force(
                    items, "assignee", start, _account(fields.get("assignee")), ids=True
                ),
                content_resolved=not broken,
                unresolved_reasons=[broken[f] for f in sorted(broken)],
                unresolved_fields=sorted(broken),  # type: ignore[arg-type]
            )
        )
    return versions


def _chain_check(
    changes: list[_Item],
    current: dict[str, str | None],
    number_of: dict[str, int],
    last_number: int,
    text: TextForms,
) -> dict[str, tuple[int, str]]:
    """Per field: (first version number whose text is verified, reason) for broken chains."""
    unresolved: dict[str, tuple[int, str]] = {}
    for field in current:
        chain = [i for i in changes if i.field == field]
        for index, item in enumerate(chain):
            if index + 1 < len(chain):
                following = chain[index + 1]
                after = _history_text(following.from_string, text)
                where = f"the 'from' text of history {following.history_id}"
                limit = number_of[following.history_id]
            else:
                after, where, limit = current[field], "the current value", last_number + 1
            if comparison_form(_history_text(item.to_string, text)) == comparison_form(after):
                continue
            reason = f"{field}: 'to' text of history {item.history_id} does not match {where}"
            if limit > unresolved.get(field, (0, ""))[0]:
                unresolved[field] = (limit, reason)
    return unresolved


def _in_force(
    items: list[_Item], field: str, at: datetime, current: str | None, *, ids: bool = False
) -> str | None:
    """Value of `field` at time `at`: the value before its first change, then each change."""
    moves = [i for i in items if i.field == field]
    if not moves:
        return current
    value = moves[0].from_id if ids else moves[0].from_string
    for move in moves:
        if move.at <= at:
            value = move.to_id if ids else move.to_string
    return value


def _name(value: Any) -> str | None:
    return value.get("name") if isinstance(value, dict) else None


def _account(value: Any) -> str | None:
    return value.get("accountId") if isinstance(value, dict) else None


# --- approvals, priority, assignee ------------------------------------------------------------


def _facts(
    key: str, fields: dict[str, Any], items: list[_Item], context: LifecycleContext
) -> LifecycleFacts:
    approvals = _approvals(key, items, context)
    valid = [a for a in approvals if a.by_approver_group]
    first_valid = min((a.at for a in valid), default=None)
    tracked = set(context.policy.requirement_fields)
    edits = [i.at for i in items if i.field in tracked]
    assignees = [i for i in items if i.field == "assignee"]
    initial_assignee = assignees[0].from_id if assignees else _account(fields.get("assignee"))
    return LifecycleFacts(
        key=key,
        requester_account_id=_account(fields.get("reporter")),
        approvals=approvals,
        latest_valid_approval_at=max((a.at for a in valid if a.revoked_at is None), default=None),
        last_requirement_edit_at=max(edits, default=None),
        priority_changes=[
            PriorityChange(
                key=key,
                actor_account_id=i.actor,
                at=i.at,
                from_priority=i.from_string,
                to_priority=i.to_string,
                after_first_approval=first_valid is not None and i.at > first_valid,
                actor_has_authority=(
                    None
                    if i.actor is None or context.authorities is None
                    else i.actor in context.authorities
                ),
                evidence_id=i.evidence_id,
            )
            for i in items
            if i.field == "priority"
        ],
        assignee_history=[(parse_time(fields["created"]), initial_assignee)]
        + [(i.at, i.to_id) for i in assignees],
        labels=[str(label) for label in fields.get("labels") or []],
    )


def _approvals(key: str, items: list[_Item], context: LifecycleContext) -> list[Approval]:
    approved = context.policy.approval.status
    moves = [i for i in items if i.field == "status"]
    approvals = []
    for index, move in enumerate(moves):
        if move.to_string != approved:
            continue
        revoked_at = None
        for later in moves[index + 1 :]:
            if later.to_string == approved:
                break
            if later.from_string == approved and later.to_string in context.new_statuses:
                revoked_at = later.at
                break
        approvals.append(
            Approval(
                key=key,
                actor_account_id=move.actor,
                at=move.at,
                history_id=move.history_id,
                by_approver_group=move.actor is not None and move.actor in context.approvers,
                revoked_at=revoked_at,
                evidence_id=move.evidence_id,
            )
        )
    return approvals
