"""Property tests for docs/specs/step-3-bundle-replay.md (AC2, AC3)."""

import random
import string
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.bundle.hashing import bundle_hash
from hypothesis import given, settings
from hypothesis import strategies as st

from codeatlas.schema import EvidenceBundle
from tests.samples import BUNDLE

IDS = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=8)
TIMES = st.datetimes(
    min_value=datetime(2020, 1, 1),  # noqa: DTZ001 (hypothesis bounds are naive)
    max_value=datetime(2030, 1, 1),  # noqa: DTZ001
    timezones=st.just(UTC),
)
TEXT = st.text(max_size=20)


@st.composite
def bundles(draw: st.DrawFn) -> EvidenceBundle:
    """BUNDLE with random, multi-element lists in the places that need sorting."""
    base = BUNDLE.model_dump()
    event = base["change_events"][0]
    events = [
        {**event, "evidence_id": f"jira:{i}", "history_id": i, "to_value": draw(TEXT), "at": at}
        for i, at in draw(st.dictionaries(IDS, TIMES, max_size=5)).items()
    ]
    candidate = base["candidates"][0]
    candidates = [
        {**candidate, "entity_id": e, "fused_rank": r, "fused_score": s}
        for (e, r), s in draw(
            st.dictionaries(
                st.tuples(IDS, st.integers(1, 5)),
                st.floats(0, 1, allow_nan=False),
                max_size=5,
            )
        ).items()
    ]
    rule = base["governance"]["owner_rules"][0]
    rules = [
        {**rule, "line": line, "owners": draw(st.lists(IDS, max_size=3))}
        for line in draw(st.sets(st.integers(1, 200), max_size=5))
    ]
    history = draw(st.lists(st.tuples(TIMES, st.one_of(st.none(), IDS)), max_size=4))
    lifecycle = {**base["lifecycle"][0], "assignee_history": history}
    data = {
        **base,
        "change_events": events,
        "candidates": candidates,
        "lifecycle": [lifecycle],
        "governance": {**base["governance"], "owner_rules": rules},
    }
    return EvidenceBundle.model_validate(data)


def shuffled(value: Any, rng: random.Random) -> Any:
    """Shuffle every list (not tuples) at every depth, and move datetimes to +05:00."""
    if isinstance(value, dict):
        return {k: shuffled(v, rng) for k, v in value.items()}
    if isinstance(value, list):
        items = [shuffled(v, rng) for v in value]
        rng.shuffle(items)
        return items
    if isinstance(value, tuple):
        return tuple(shuffled(v, rng) for v in value)
    if isinstance(value, datetime):
        return value.astimezone(timezone(timedelta(hours=5)))
    return value


@settings(max_examples=200, deadline=None)
@given(bundle=bundles(), rng=st.randoms(use_true_random=False))
def test_ac2_canonical_form_ignores_list_order_and_time_zone(
    bundle: EvidenceBundle, rng: random.Random
) -> None:
    other = EvidenceBundle.model_validate(shuffled(bundle.model_dump(), rng))

    assert canonical_bytes(other) == canonical_bytes(bundle)
    assert bundle_hash(other) == bundle_hash(bundle)


@settings(max_examples=200, deadline=None)
@given(bundle=bundles())
def test_ac3_canonical_form_is_idempotent_and_hash_is_stable(bundle: EvidenceBundle) -> None:
    first = canonical_bytes(bundle)

    reloaded = EvidenceBundle.model_validate_json(first)

    assert canonical_bytes(reloaded) == first
    assert bundle_hash(reloaded) == bundle_hash(bundle) == bundle_hash(bundle)
