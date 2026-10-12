"""Tests for docs/specs/step-2-schema.md (AC1-AC8). Samples live in tests/samples.py."""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import codeatlas.schema as schema
from codeatlas.schema import (
    Approval,
    ApprovalPolicy,
    AuthorityPolicy,
    EvidenceBundle,
    LifecycleFacts,
    Policy,
    RequirementVersion,
)
from tests.samples import (
    APPROVAL,
    BUNDLE,
    CHECK,
    EVIDENCE,
    LIFECYCLE,
    POLICY,
    REVIEW,
    SAMPLES,
    T0,
    TRACE_LINK,
    VERDICT,
    VERSION,
    WORK_ITEM,
)


def sample_id(sample: BaseModel) -> str:
    return type(sample).__name__


# --- AC1: round trip, and every exported model has a sample ------------------------------------


def test_ac1_every_exported_model_has_a_sample() -> None:
    exported = {
        name
        for name in schema.__all__
        if isinstance(getattr(schema, name), type) and issubclass(getattr(schema, name), BaseModel)
    }

    assert exported == {type(sample).__name__ for sample in SAMPLES}


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac1_json_round_trip(sample: BaseModel) -> None:
    first = sample.model_dump_json()

    loaded = type(sample).model_validate_json(first)

    assert loaded == sample
    assert loaded.model_dump_json() == first


# --- AC2-AC3: unknown fields rejected, frozen --------------------------------------------------


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac2_unknown_field_rejected(sample: BaseModel) -> None:
    data = {**sample.model_dump(), "unexpected": 1}

    with pytest.raises(ValidationError, match="unexpected"):
        type(sample).model_validate(data)


@pytest.mark.parametrize("sample", SAMPLES, ids=sample_id)
def test_ac3_models_are_frozen(sample: BaseModel) -> None:
    field = next(iter(type(sample).model_fields))

    with pytest.raises(ValidationError, match="frozen"):
        setattr(sample, field, getattr(sample, field))


# --- AC4: datetimes are UTC-aware ---------------------------------------------------------------


def test_ac4_naive_datetime_rejected() -> None:
    naive = datetime(2026, 10, 3, 9, 0)  # noqa: DTZ001

    with pytest.raises(ValidationError, match="timezone"):
        Approval.model_validate({**APPROVAL.model_dump(), "at": naive})


def test_ac4_naive_datetime_rejected_inside_tuples() -> None:
    naive = [(datetime(2026, 10, 3, 9, 0), "user-02")]  # noqa: DTZ001

    with pytest.raises(ValidationError, match="timezone"):
        LifecycleFacts.model_validate({**LIFECYCLE.model_dump(), "assignee_history": naive})


def test_ac4_aware_datetime_converted_to_utc() -> None:
    plus_five = datetime(2026, 10, 3, 9, 0, tzinfo=timezone(timedelta(hours=5)))

    approval = Approval.model_validate({**APPROVAL.model_dump(), "at": plus_five})

    assert approval.at == T0
    assert approval.at.utcoffset() == timedelta(0)
    assert '"at":"2026-10-03T04:00:00Z"' in approval.model_dump_json()


# --- AC5: literal values are closed -------------------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "field", "value"),
    [
        (TRACE_LINK, "state", "LINKED"),
        (REVIEW, "state", "PENDING"),
        (WORK_ITEM, "found_in", "title"),
        (EVIDENCE, "source", "linear"),
        (VERSION, "unresolved_fields", ["status"]),
        (BUNDLE, "schema_version", "1"),
        (CHECK, "case", "C10"),
        (CHECK, "outcome", "satisfied"),
        (VERDICT, "value", "PASSED"),
    ],
    ids=[
        "link-state",
        "review-state",
        "found-in",
        "source",
        "requirement-field",
        "schema-version",
        "case-id",
        "outcome",
        "verdict",
    ],
)
def test_ac5_unknown_literal_values_rejected(sample: BaseModel, field: str, value: Any) -> None:
    with pytest.raises(ValidationError, match=field):
        type(sample).model_validate({**sample.model_dump(), field: value})


# --- AC6: the whole bundle --------------------------------------------------------------------


def test_ac6_bundle_dumps_identically_and_round_trips() -> None:
    first = BUNDLE.model_dump_json()

    assert BUNDLE.model_dump_json() == first
    assert EvidenceBundle.model_validate_json(first) == BUNDLE
    assert EvidenceBundle.model_validate_json(first).model_dump_json() == first


def test_ac6_bundle_has_no_bundle_hash_field() -> None:
    # S3: the bundle is content-addressed, so its hash cannot live inside it.
    assert "bundle_hash" not in EvidenceBundle.model_fields


# --- AC8: approvers and priority authority (S1) -------------------------------------------------


@pytest.mark.parametrize(
    "policy",
    [
        ApprovalPolicy(status="Approved", approver_group="sbx-requirement-approvers"),
        ApprovalPolicy(status="Approved", approver_account_ids=["user-01"]),
        ApprovalPolicy(status="Approved", approver_group="g", approver_account_ids=["user-01"]),
        AuthorityPolicy(group="sbx-priority-authority"),
        AuthorityPolicy(account_ids=["user-01", "user-02"]),
    ],
    ids=["approval-group", "approval-ids", "approval-both", "authority-group", "authority-ids"],
)
def test_ac8_group_or_account_ids_accepted(policy: BaseModel) -> None:
    assert type(policy).model_validate(policy.model_dump()) == policy


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (ApprovalPolicy, {"status": "Approved"}),
        (ApprovalPolicy, {"status": "Approved", "approver_account_ids": []}),
        (AuthorityPolicy, {}),
        (AuthorityPolicy, {"group": None, "account_ids": []}),
    ],
    ids=["approval-nothing", "approval-empty-ids", "authority-nothing", "authority-empty-ids"],
)
def test_ac8_neither_group_nor_account_ids_rejected(
    model: type[BaseModel], data: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError, match="group or at least one account id"):
        model.model_validate(data)


def test_ac8_policy_requires_priority_authority() -> None:
    data = POLICY.model_dump()
    del data["priority_authority"]

    with pytest.raises(ValidationError, match="priority_authority"):
        Policy.model_validate({**data, "priority_authority_group": "sbx-priority-authority"})


# --- AC7: version contents (S5) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_resolved", "unresolved_fields"),
    [(True, ["description"]), (False, [])],
)
def test_ac7_content_resolved_must_match_unresolved_fields(
    content_resolved: bool, unresolved_fields: list[str]
) -> None:
    data = {
        **VERSION.model_dump(),
        "content_resolved": content_resolved,
        "unresolved_fields": unresolved_fields,
    }

    with pytest.raises(ValidationError, match="content_resolved"):
        RequirementVersion.model_validate(data)


def test_ac7_unresolved_field_must_have_no_text() -> None:
    data = {**VERSION.model_dump(), "description": "guessed text"}

    with pytest.raises(ValidationError, match="description"):
        RequirementVersion.model_validate(data)


def test_ac7_fully_resolved_version_is_valid() -> None:
    data = {
        **VERSION.model_dump(),
        "description": "A member returns a book.",
        "content_resolved": True,
        "unresolved_reasons": [],
        "unresolved_fields": [],
    }

    assert RequirementVersion.model_validate(data).content_resolved
