"""Shared literal types, the UTC datetime type and the frozen base model (spec §7.1, §15)."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict

CaseId = Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"]
Outcome = Literal["SATISFIED", "VIOLATED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"]
LinkState = Literal[
    "OBSERVED", "DETERMINISTICALLY_DERIVED", "SEMANTIC_CANDIDATE", "CONFLICTING", "UNRESOLVED"
]
VerdictValue = Literal["PASS", "FAIL", "REVIEW", "UNKNOWN"]
RequirementField = Literal["summary", "description", "acceptance_criteria"]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Naive datetimes are rejected; aware ones are stored in UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class Model(BaseModel):
    """Base for every schema model: immutable, and unknown fields are an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")
