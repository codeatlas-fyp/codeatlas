"""Comparison must preserve uncertainty when evidence is missing."""

import pytest

from codeatlas.api.comparison import _finding_change
from codeatlas.schema import CheckResult


def _result(outcome: str, severity: str = "none") -> CheckResult:
    return CheckResult.model_validate(
        {
            "case": "C5",
            "outcome": outcome,
            "severity": severity,
            "required": True,
            "message": "evidence",
            "evidence_ids": [],
        }
    )


@pytest.mark.parametrize(
    "before,after,expected",
    [
        (_result("VIOLATED", "block"), _result("SATISFIED"), "resolved"),
        (_result("SATISFIED"), _result("VIOLATED", "review"), "new"),
        (_result("VIOLATED", "block"), _result("VIOLATED", "review"), "severity_changed"),
        (_result("VIOLATED", "block"), _result("INSUFFICIENT_EVIDENCE"), "not_comparable"),
        (_result("VIOLATED", "block"), None, "not_comparable"),
        (_result("VIOLATED", "block"), _result("NOT_APPLICABLE"), "changed"),
    ],
)
def test_finding_changes_do_not_resolve_missing_evidence(before, after, expected) -> None:
    assert _finding_change(before, after) == expected
