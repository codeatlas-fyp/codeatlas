"""Tests for docs/specs/step-4-rule-framework.md (AC1, AC2, AC4-AC8)."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import BundleStore, load_verdict
from codeatlas.errors import EvidenceValidationError
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.base import Rule
from codeatlas.evaluate.verdict import decide, run_rules
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import (
    CaseId,
    CheckResult,
    EvidenceBundle,
    Outcome,
    Policy,
    TraceLink,
)
from tests.samples import BUNDLE

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bundles"


def result(
    case: CaseId = "C1",
    outcome: Outcome = "SATISFIED",
    severity: str = "review",
    required: bool = True,
) -> CheckResult:
    return CheckResult.model_validate(
        {
            "case": case,
            "outcome": outcome,
            "severity": severity,
            "required": required,
            "message": f"{case} {outcome}",
            "evidence_ids": [],
        }
    )


@dataclass
class FixedRule:
    """A rule that returns a fixed result; `returns_case` lets a test break the contract."""

    case: CaseId
    outcome: Outcome = "SATISFIED"
    severity: str = "review"
    version: str = "1"
    required_evidence: tuple[str, ...] = ("versions",)
    returns_case: CaseId | None = None
    seen: list[str] = field(default_factory=list)

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        self.seen.append(bundle.run_id)
        return result(self.returns_case or self.case, self.outcome, self.severity)


class ExplodingRule:
    case: CaseId = "C8"
    version = "1"
    required_evidence = ("lifecycle",)

    def check(self, bundle: EvidenceBundle, links: list[TraceLink], policy: Policy) -> CheckResult:
        raise ZeroDivisionError("bug in rule")


# --- AC1-AC2: truth table ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([result("C3", "VIOLATED", "block"), result("C1", "VIOLATED", "review")], "FAIL"),
        (
            [result("C1", "VIOLATED", "review"), result("C7", "INSUFFICIENT_EVIDENCE")],
            "REVIEW",
        ),
        ([result("C7", "INSUFFICIENT_EVIDENCE"), result("C1", "SATISFIED")], "UNKNOWN"),
        ([result("C1", "SATISFIED"), result("C3", "NOT_APPLICABLE")], "PASS"),
        ([], "PASS"),
        ([result("C1", "VIOLATED", "none")], "PASS"),
        ([result("C3", "VIOLATED", "block")], "FAIL"),
    ],
    ids=[
        "row1-block-beats-review",
        "row2-review-beats-unknown",
        "row3-required-insufficient",
        "row4-satisfied-and-not-applicable",
        "row4-no-results",
        "row4-violation-without-severity",
        "row1-block-alone",
    ],
)
def test_ac1_truth_table(results: list[CheckResult], expected: str) -> None:
    assert decide(results) == expected


def test_ac2_optional_insufficient_evidence_does_not_make_unknown() -> None:
    assert decide([result("C7", "INSUFFICIENT_EVIDENCE", required=False)]) == "PASS"


# --- AC4: rule contract -------------------------------------------------------------------


def test_ac4_results_sorted_by_case_and_rules_see_the_bundle() -> None:
    rules = [FixedRule("C8"), FixedRule("C1"), FixedRule("C3")]

    results = run_rules(rules, BUNDLE, [])

    assert [r.case for r in results] == ["C1", "C3", "C8"]
    assert all(rule.seen == [BUNDLE.run_id] for rule in rules)


def test_ac4_rule_returning_another_case_is_rejected() -> None:
    with pytest.raises(ValueError, match="C1 returned a result for C3"):
        run_rules([FixedRule("C1", returns_case="C3")], BUNDLE, [])


def test_ac4_duplicate_cases_are_rejected() -> None:
    with pytest.raises(ValueError, match="more than one rule for C1"):
        run_rules([FixedRule("C1"), FixedRule("C1")], BUNDLE, [])


# --- AC5: a rule that raises ---------------------------------------------------------------


def test_ac5_rule_that_raises_gives_exit_4_and_no_verdict() -> None:
    with pytest.raises(EvidenceValidationError, match="C8 failed") as raised:
        evaluate_bundle(BUNDLE, "a" * 64, rules=[FixedRule("C1"), ExplodingRule()])

    assert raised.value.exit_code == 4


# --- AC6: ruleset version ------------------------------------------------------------------


def test_ac6_ruleset_version() -> None:
    first = ruleset_version([FixedRule("C3"), FixedRule("C1")])

    assert first == "C1@1,C3@1"
    assert ruleset_version([FixedRule("C1"), FixedRule("C3")]) == first
    assert ruleset_version([FixedRule("C1", version="2"), FixedRule("C3")]) != first
    assert ruleset_version([]) == "none"


# --- AC7: evaluate_bundle is the replay evaluator ------------------------------------------


def test_ac7_evaluate_bundle_seals_value_and_hash() -> None:
    rules = [FixedRule("C1", "VIOLATED", "review"), FixedRule("C3")]

    verdict = evaluate_bundle(BUNDLE, "a" * 64, rules=rules)

    assert verdict.value == "REVIEW"
    assert verdict.bundle_hash == "a" * 64
    assert verdict.ruleset_version == "C1@1,C3@1"
    assert len(verdict.verdict_hash) == 64


def test_ac7_sample_bundle_replays_with_the_default_rules() -> None:
    (folder,) = [p for p in FIXTURES.iterdir() if p.is_dir()]

    result_ = replay(folder, evaluate_bundle)

    assert result_.match
    # The sample's lifecycle was approved at T0 and edited at T1, so C3 sees a stale approval.
    assert load_verdict(folder).value == "FAIL"
    assert load_verdict(folder).ruleset_version == ruleset_version(default_rules())


def test_ac7_stored_verdict_survives_a_round_trip(tmp_path: Path) -> None:
    store = BundleStore(tmp_path)
    hash_ = store.save(BUNDLE)
    rules = [FixedRule("C3", "VIOLATED", "block")]
    store.save_verdict(evaluate_bundle(BUNDLE, hash_, rules=rules))

    def same_rules(bundle: EvidenceBundle, bundle_hash: str) -> object:
        return evaluate_bundle(bundle, bundle_hash, rules=rules)

    assert replay(tmp_path / hash_, same_rules).match  # type: ignore[arg-type]


# --- AC8: protocol and declared evidence ---------------------------------------------------


def test_ac8_fixed_rule_satisfies_the_protocol() -> None:
    rule: Rule = FixedRule("C1")

    assert rule.case == "C1"


def test_ac8_required_evidence_names_bundle_fields() -> None:
    fields = set(EvidenceBundle.model_fields)

    for rule in default_rules():
        assert set(rule.required_evidence) <= fields, rule.case
