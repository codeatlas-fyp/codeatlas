"""Version and uncertainty checks for application integration payloads."""

import pytest
from pydantic import ValidationError

from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.schema.integration import GraphView, SemanticRun


def test_unavailable_graph_round_trips_without_invented_edges() -> None:
    graph = GraphView.model_validate(
        {
            "status": "unavailable",
            "snapshot": {"repo": "owner/repo", "base_sha": None, "head_sha": None},
            "limitations": ["graph export not supplied"],
        }
    )
    assert GraphView.model_validate_json(graph.model_dump_json()) == graph
    assert graph.nodes == [] and graph.edges == []


def test_gate_aware_c9_has_a_new_ruleset_version() -> None:
    assert "C9@1" in ruleset_version(default_rules())
    assert "C9@2" in ruleset_version(default_rules(semantic_run=True))


def test_semantic_abstention_round_trips_without_candidates() -> None:
    run = SemanticRun(criterion_id="ISSUE:v1:ac1", status="abstained", reason="uncertain")
    assert SemanticRun.model_validate_json(run.model_dump_json()) == run
    assert run.candidates == []


def test_accepted_semantic_run_requires_recorded_provenance() -> None:
    with pytest.raises(ValidationError, match="provenance"):
        SemanticRun.model_validate(
            {
                "criterion_id": "ISSUE:v1:ac1",
                "status": "accepted",
                "reason": "accepted by gate",
                "candidates": [{"entity_id": "pkg.f", "rank": 1, "accepted": True}],
            }
        )


def test_accepted_gate_does_not_invent_which_candidate_was_accepted() -> None:
    run = SemanticRun.model_validate(
        {
            "criterion_id": "ISSUE:v1:ac1",
            "status": "accepted",
            "reason": "gate accepted the criterion",
            "source_revision": "abc",
            "candidate_universe_hash": "hash",
            "model_id": "model@revision",
            "representation_version": "body-v1",
            "gate_profile": "profile",
            "candidates": [{"entity_id": "pkg.f", "rank": 1, "accepted": None}],
        }
    )
    assert run.candidates[0].accepted is None


@pytest.mark.parametrize(
    "model,payload",
    [
        (GraphView, {"schema_version": "graph-view-v99", "status": "unavailable"}),
        (SemanticRun, {"schema_version": "semantic-run-v99", "criterion_id": "x"}),
    ],
)
def test_unrecognized_schema_version_is_rejected(model, payload) -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        model.model_validate(payload)
