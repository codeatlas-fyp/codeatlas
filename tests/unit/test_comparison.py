"""Comparison must preserve uncertainty when evidence is missing."""

import pytest

from codeatlas.api.cases import Case, _summary
from codeatlas.api.comparison import _finding_change, compare_cases
from codeatlas.schema import CheckResult
from tests.samples import BUNDLE, VERDICT


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


def test_comparison_discloses_missing_or_different_source_modes() -> None:
    fixture = BUNDLE.model_copy(update={"collection_notes": ["explicit source mode: fixtures"]})
    live = BUNDLE.model_copy(update={"collection_notes": ["explicit source mode: live"]})
    left = Case(summary=_summary("a" * 64, fixture, VERDICT), bundle=fixture, verdict=VERDICT)
    right = Case(summary=_summary("b" * 64, live, VERDICT), bundle=live, verdict=VERDICT)
    result = compare_cases(left, right, "left", "right")
    assert "Different source modes." in result["limitations"]
    assert result["before"]["source_mode"] == "fixtures"
    assert all(finding["change"] == "not_comparable" for finding in result["findings"])
    unknown = BUNDLE.model_copy(update={"collection_notes": []})
    right = Case(summary=_summary("b" * 64, unknown, VERDICT), bundle=unknown, verdict=VERDICT)
    result = compare_cases(left, right, "left", "right")
    assert (
        "Source mode is missing or ambiguous for at least one evaluation." in result["limitations"]
    )
