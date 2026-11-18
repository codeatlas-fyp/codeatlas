"""Version and uncertainty checks for application integration payloads."""

import pytest
from pydantic import ValidationError

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
