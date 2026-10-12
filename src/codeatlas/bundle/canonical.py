"""Canonical JSON bytes for hashing (spec §7.4; step-3 spec "Canonical form").

Same evidence gives the same bytes on every machine, whatever the input order of lists, the time
zone of datetimes, or sub-second noise.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from codeatlas.schema import (
    Adr,
    Approval,
    CheckResult,
    CodeEdge,
    CodeEntity,
    Criterion,
    Evidence,
    IdentityEntry,
    LifecycleFacts,
    OwnerRule,
    PriorityChange,
    RequirementVersion,
    ResolvedPerson,
    SemanticCandidate,
    TraceLink,
)

Identifier = tuple[str | int, ...]

_IDENTIFIERS: dict[type[BaseModel], Callable[[Any], Identifier]] = {
    Evidence: lambda m: (m.evidence_id,),
    Approval: lambda m: (m.evidence_id,),
    PriorityChange: lambda m: (m.evidence_id,),
    RequirementVersion: lambda m: (m.key, m.version_no),
    LifecycleFacts: lambda m: (m.key,),
    ResolvedPerson: lambda m: (m.git_email or "", m.github_login or "", m.jira_account_id or ""),
    OwnerRule: lambda m: (m.line,),
    Adr: lambda m: (m.adr_id,),
    CodeEntity: lambda m: (m.entity_id,),
    CodeEdge: lambda m: (m.src, m.dst, m.kind),
    Criterion: lambda m: (m.criterion_id,),
    SemanticCandidate: lambda m: (m.criterion_id, m.fused_rank, m.entity_id),
    TraceLink: lambda m: (m.requirement_key, m.criterion_id or "", m.entity_id),
    CheckResult: lambda m: (m.case,),
    IdentityEntry: lambda m: (m.jira_account_id,),
}


def _identifier_for(model: type[BaseModel]) -> Callable[[Any], Identifier] | None:
    for cls in model.__mro__:
        if cls in _IDENTIFIERS:
            return _IDENTIFIERS[cls]
    return None


def has_identifier(model: type[BaseModel]) -> bool:
    """True if instances of `model` can be sorted inside a list (AC9 guard)."""
    return _identifier_for(model) is not None


def _plain(value: Any) -> Any:
    """Turn models, datetimes, floats and tuples into JSON values; sort every list."""
    if isinstance(value, BaseModel):
        return {name: _plain(getattr(value, name)) for name in type(value).model_fields}
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be made canonical")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in sorted(value, key=_sort_key)]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    raise TypeError(f"cannot make {type(value).__name__} canonical")


def _sort_key(item: Any) -> tuple[Identifier, str]:
    """Identifier first; the full canonical text breaks ties, so order never depends on input."""
    text = _dumps(_plain(item))
    if isinstance(item, BaseModel):
        identify = _identifier_for(type(item))
        if identify is None:
            raise TypeError(f"{type(item).__name__} has no canonical identifier")
        return identify(item), text
    if isinstance(item, str):
        return (item,), text
    return (), text


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(value: Any) -> bytes:
    """UTF-8 canonical JSON of a schema model, or of a dict or list of them."""
    return _dumps(_plain(value)).encode("utf-8")
