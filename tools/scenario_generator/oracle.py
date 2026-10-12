"""Expected results for a generated issue, worked out from its action log (pure, no I/O).

This is the ground truth the evaluation compares CodeAtlas against. It is written independently
of `codeatlas.evaluate.rules` (it does not import them): it replays the actions the generator
actually performed, with Jira's own timestamps.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Outcome = Literal["SATISFIED", "VIOLATED"]


@dataclass(frozen=True)
class Done:
    """One action as performed, at Jira's timestamp (commit markers: the chosen commit time)."""

    kind: str
    at: datetime
    text: str | None = None


@dataclass(frozen=True)
class Expected:
    versions: list[str]  # description text of every version, oldest first
    c1: Outcome
    c3: Outcome
    c3_reason: Literal["approved", "not approved", "stale approval"]
    c6: Outcome
    c8_with_authority: Outcome
    c8_without_authority: Outcome


def expected(initial_description: str, log: list[Done]) -> Expected:
    log = sorted(log, key=lambda d: d.at)
    first_commit = next(d.at for d in log if d.kind == "first_commit")
    last_commit = next(d.at for d in log if d.kind == "last_commit")
    edits = [d for d in log if d.kind == "edit"]

    approvals: list[tuple[datetime, datetime | None]] = []  # (approved at, revoked at)
    for d in log:
        if d.kind == "approve":
            approvals.append((d.at, None))
        elif d.kind == "unapprove" and approvals and approvals[-1][1] is None:
            approvals[-1] = (approvals[-1][0], d.at)
    valid = [at for at, revoked in approvals if revoked is None]
    latest_valid = max(valid, default=None)
    last_edit = max((d.at for d in edits), default=None)
    if latest_valid is None:
        c3, reason = "VIOLATED", "not approved"
    elif last_edit is not None and last_edit > latest_valid:
        c3, reason = "VIOLATED", "stale approval"
    else:
        c3, reason = "SATISFIED", "approved"

    first_approval = min((at for at, _ in approvals), default=None)
    priority_after_approval = any(
        d.kind == "priority" and first_approval is not None and d.at > first_approval for d in log
    )
    return Expected(
        versions=[initial_description] + [d.text or "" for d in edits],
        c1="VIOLATED" if any(d.at > first_commit for d in edits) else "SATISFIED",
        c3=c3,  # type: ignore[arg-type]
        c3_reason=reason,  # type: ignore[arg-type]
        c6="VIOLATED" if any(d.at > last_commit for d in edits) else "SATISFIED",
        c8_with_authority="SATISFIED",
        c8_without_authority="VIOLATED" if priority_after_approval else "SATISFIED",
    )
