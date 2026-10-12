"""Property test for docs/specs/step-4-rule-framework.md AC3: rule order never matters."""

from hypothesis import given, settings
from hypothesis import strategies as st

from codeatlas.pipeline import evaluate_bundle
from tests.samples import BUNDLE
from tests.unit.test_verdict import FixedRule

CASES = st.sampled_from(["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"])
OUTCOMES = st.sampled_from(["SATISFIED", "VIOLATED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"])
SEVERITIES = st.sampled_from(["block", "review", "none"])


@st.composite
def rule_sets(draw: st.DrawFn) -> list[FixedRule]:
    cases = draw(st.lists(CASES, unique=True, max_size=9))
    return [
        FixedRule(case, draw(OUTCOMES), draw(SEVERITIES), version=draw(st.sampled_from("12")))
        for case in cases
    ]


@settings(max_examples=300, deadline=None)
@given(rules=rule_sets(), data=st.data())
def test_ac3_verdict_independent_of_rule_order(rules: list[FixedRule], data: st.DataObject) -> None:
    permuted = data.draw(st.permutations(rules))

    first = evaluate_bundle(BUNDLE, "a" * 64, rules=rules)
    second = evaluate_bundle(BUNDLE, "a" * 64, rules=permuted)

    assert second == first
