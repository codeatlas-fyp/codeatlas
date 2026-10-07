"""Property tests for docs/specs/step-7-rules.md AC4 and AC5."""

import random

from hypothesis import given, settings
from hypothesis import strategies as st

from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import EvidenceBundle
from tests.unit.test_rules import approval, bundle, edit, facts, raise_priority


@st.composite
def bundles(draw: st.DrawFn, *, resolved: bool | None = None) -> EvidenceBundle:
    approvals = [
        approval(m, valid=draw(st.booleans()))
        for m in draw(st.lists(st.integers(1, 50), max_size=3, unique=True))
    ]
    edit_times = draw(st.lists(st.integers(1, 50), max_size=4, unique=True))
    priorities = [
        raise_priority(
            m,
            after=draw(st.booleans()),
            authority=draw(st.sampled_from([True, False, None])),
            actor=draw(st.sampled_from(["user-01", "user-03", None])),
        )
        for m in draw(st.lists(st.integers(1, 50), max_size=3, unique=True))
    ]
    return bundle(
        lifecycle=[facts(approvals=approvals, edits=edit_times, priorities=priorities)],
        edits=[edit(m) for m in edit_times],
        commits=draw(st.lists(st.integers(1, 50), max_size=3, unique=True)),
        resolved=draw(st.booleans()) if resolved is None else resolved,
    )


@settings(max_examples=300, deadline=None)
@given(b=bundles(resolved=False))
def test_ac4_broken_chain_never_passes(b: EvidenceBundle) -> None:
    assert evaluate_bundle(b, "a" * 64).value != "PASS"


@settings(max_examples=300, deadline=None)
@given(b=bundles(), seed=st.integers())
def test_ac5_rules_ignore_list_order(b: EvidenceBundle, seed: int) -> None:
    rng = random.Random(seed)
    shuffled = b.model_copy(
        update={
            "change_events": rng.sample(b.change_events, len(b.change_events)),
            "commits": rng.sample(b.commits, len(b.commits)),
        }
    )

    assert evaluate_bundle(shuffled, "a" * 64) == evaluate_bundle(b, "a" * 64)
