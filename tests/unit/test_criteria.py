"""Tests for `analyze.criteria.split_criteria`."""

from __future__ import annotations

from codeatlas.analyze.criteria import split_criteria


def test_bullet_criteria_under_header() -> None:
    description = """\
As a user I want late fees so that overdue books are returned.

Acceptance Criteria:
- Fees shown to two decimal places
- Fees capped at $10.00
- First offence under one week is waivable
"""
    criteria = split_criteria(
        requirement_key="SBX-4", version_no=1, description=description,
    )
    assert len(criteria) == 3
    assert criteria[0].text == "Fees shown to two decimal places"
    assert criteria[0].criterion_id == "SBX-4:v1:ac1"
    assert criteria[0].derived_from_description is False
    assert criteria[2].text == "First offence under one week is waivable"


def test_numbered_criteria_under_header() -> None:
    description = """\
Acceptance:
1. Daily fee must be configurable
2. Max fee must be configurable
"""
    criteria = split_criteria(
        requirement_key="SBX-5", version_no=2, description=description,
    )
    assert len(criteria) == 2
    assert criteria[0].text == "Daily fee must be configurable"
    assert criteria[1].criterion_id == "SBX-5:v2:ac2"


def test_fallback_to_sentence_split_when_no_header() -> None:
    description = (
        "Users must be able to pay fees. "
        "Fees are deducted from the user's account. "
        "A receipt is emailed to the user."
    )
    criteria = split_criteria(
        requirement_key="SBX-6", version_no=1, description=description,
    )
    assert len(criteria) == 3
    for crit in criteria:
        assert crit.derived_from_description is True
    assert "pay fees" in criteria[0].text


def test_empty_description_returns_empty() -> None:
    assert split_criteria(requirement_key="X-1", version_no=1, description="") == []
    assert split_criteria(requirement_key="X-1", version_no=1, description="   ") == []


def test_header_variants_case_insensitive() -> None:
    for header in ("acceptance criteria:", "ACCEPTANCE CRITERIA", "AC:", "criteria"):
        description = f"intro\n\n{header}\n\n- First\n- Second\n"
        criteria = split_criteria(requirement_key="X-1", version_no=1, description=description)
        assert len(criteria) == 2
        assert criteria[0].derived_from_description is False


def test_criterion_ids_are_stable_and_ordered() -> None:
    description = "AC:\n- A\n- B\n- C\n- D\n- E\n"
    criteria = split_criteria(requirement_key="X-1", version_no=1, description=description)
    assert [c.criterion_id for c in criteria] == [f"X-1:v1:ac{i}" for i in range(1, 6)]
