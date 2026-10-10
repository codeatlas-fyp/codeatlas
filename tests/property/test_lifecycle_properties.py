"""Property tests for docs/specs/step-6-lifecycle.md AC2 and AC4."""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from codeatlas.analyze.lifecycle import reconstruct
from tests.jira_builders import TEXT, context, history, issue, item, source

WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6)
LINE = st.lists(WORD, min_size=1, max_size=5).map(" ".join)
TEXT_VALUE = st.lists(LINE, min_size=1, max_size=3).map("\n".join)
FIELDS = ("summary", "description")


@st.composite
def edit_histories(
    draw: st.DrawFn,
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, str]]]:
    """Start values, a changelog of edits, and the expected state after each version."""
    state = {"summary": draw(LINE), "description": draw(TEXT_VALUE)}
    states = [dict(state)]
    histories = []
    for history_id in range(1, draw(st.integers(0, 6)) + 1):
        fields = draw(st.lists(st.sampled_from(FIELDS), min_size=1, max_size=2, unique=True))
        items = []
        for field in fields:
            new = draw(LINE if field == "summary" else TEXT_VALUE)
            items.append(item(field, state[field], new))
            state[field] = new
        histories.append(history(history_id, history_id * 10, *items))
        states.append(dict(state))
    return states[-1], histories, states


@settings(max_examples=300, deadline=None)
@given(data=edit_histories())
def test_ac2_forward_replay_reproduces_every_version(
    data: tuple[dict[str, str], list[dict[str, Any]], list[dict[str, str]]],
) -> None:
    current, histories, states = data
    result = reconstruct(
        source(issue(summary=current["summary"], description=current["description"]), *histories),
        context(),
        TEXT,
    )

    assert [(v.summary, v.description) for v in result.versions] == [
        (s["summary"], s["description"]) for s in states
    ]
    assert all(v.content_resolved for v in result.versions)

    # Replay the change events forward from version 1.
    replayed = {
        "summary": result.versions[0].summary,
        "description": result.versions[0].description,
    }
    for version in result.versions[1:]:
        for event in [e for e in result.events if e.at == version.valid_from]:
            replayed[event.field] = event.to_value
        assert (replayed["summary"], replayed["description"]) == (
            version.summary,
            version.description,
        )
    assert replayed == current


@settings(max_examples=300, deadline=None)
@given(data=edit_histories(), choice=st.data())
def test_ac4_corrupted_chain_is_never_fully_resolved(
    data: tuple[dict[str, str], list[dict[str, Any]], list[dict[str, str]]],
    choice: st.DataObject,
) -> None:
    current, histories, _ = data
    items = [(h, i) for h in histories for i in h["items"]]
    if not items:
        return
    target_history, target = choice.draw(st.sampled_from(items))
    # Corrupt either the newest `to` of a field, or a `from` that has an earlier change before it.
    same_field = [i for _, i in items if i["field"] == target["field"]]
    if same_field.index(target) == 0:
        target = same_field[-1]
        target["toString"] = target["toString"] + " corrupted"
    else:
        target["fromString"] = target["fromString"] + " corrupted"
    del target_history

    result = reconstruct(
        source(issue(summary=current["summary"], description=current["description"]), *histories),
        context(),
        TEXT,
    )

    assert not all(v.content_resolved for v in result.versions)
    assert len(result.events) == len(items)
