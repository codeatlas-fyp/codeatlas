"""graph-view-v1 projection: every node and edge comes from the bundle, nothing is inferred."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from codeatlas.bundle.store import load_bundle
from codeatlas.evaluate.links import bundle_links
from codeatlas.graph import build_graph_view
from codeatlas.graph.view import LEGACY_NO_CALLS, NO_INFERENCE, STATIC_EDGES
from codeatlas.schema import ChangedEntity, CodeEdge, CodeEntity, EvidenceBundle, TraceLink
from codeatlas.schema.integration import GraphView
from codeatlas.schema.types import LinkState
from tests.samples import BUNDLE, CHANGED, CRITERION, ENTITY, evidence

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "graph_view"
DEMO = ROOT / "deploy" / "bundles"
DEMO_HASH = "1ec84cb5578d0df98f5fc198d0833356ee60fb0e9bfd7963d38becaaf1d4abbc"

HEAD = "b" * 40
MERGE_BASE = "c" * 40
PR = f"change:codeatlas-fyp/codeatlas-sandbox#2@{HEAD}"
RETURN_BOOK = f"code:library.loans.return_book@{HEAD}"
CHARGE = f"code:library.fees.charge@{HEAD}"
LOANS = f"code:library.loans@{HEAD}"
FEES = f"code:library.fees@{HEAD}"


def _entity(entity_id: str, kind: str, file: str, start: int = 1, end: int = 40) -> CodeEntity:
    return ENTITY.model_copy(
        update={
            "entity_id": entity_id,
            "kind": kind,
            "file": file,
            "start_line": start,
            "end_line": end,
        }
    )


ENTITIES = [
    _entity("library.loans", "module", "library/loans.py"),
    ENTITY,  # library.loans.return_book, lines 10-30
    _entity("library.fees", "module", "library/fees.py"),
    _entity("library.fees.charge", "function", "library/fees.py", 3, 9),
]
EDGES = [
    CodeEdge(src="library.loans", dst="library.fees", kind="IMPORTS", confident=True),
    CodeEdge(src="library.loans", dst="dataclasses", kind="IMPORTS", confident=True),
    CodeEdge(
        src="library.loans.return_book", dst="library.fees.charge", kind="CALLS", confident=True
    ),
    CodeEdge(src="library.fees.charge", dst="library.loans", kind="CALLS", confident=False),
]
VERSIONS = {"jira": "0.1.0", "code": "code@0.1.0", "calls": "calls@0.3.0"}


def _bundle(**update: Any) -> EvidenceBundle:
    base = {"entities": ENTITIES, "edges": EDGES, "extractor_versions": VERSIONS}
    return BUNDLE.model_copy(update=base | update)


def _link(entity_id: str, state: LinkState, criterion_id: str | None = None) -> TraceLink:
    return TraceLink(
        requirement_key="SBX-2",
        criterion_id=criterion_id,
        entity_id=entity_id,
        state=state,
        evidence_ids=[f"evidence:{state.lower()}"],
        reasons=[],
    )


def _edges(view: GraphView, kind: str) -> list[tuple[str, str, bool | None, str | None]]:
    return [(e.src, e.dst, e.confident, e.resolution_status) for e in view.edges if e.kind == kind]


def test_nodes_cover_the_change_requirements_criteria_and_code() -> None:
    view = build_graph_view(_bundle(), [])
    kinds = {n.id: n.kind for n in view.nodes}
    assert kinds == {
        PR: "change",
        "req:SBX-2": "requirement",
        "crit:SBX-2:v2:ac1": "criterion",
        LOANS: "module",
        RETURN_BOOK: "function",
        FEES: "module",
        CHARGE: "function",
    }
    node = next(n for n in view.nodes if n.id == RETURN_BOOK)
    assert (node.entity_id, node.file, node.start_line, node.end_line, node.revision) == (
        "library.loans.return_book",
        "library/loans.py",
        10,
        30,
        HEAD,
    )
    assert view.snapshot.model_dump() == {
        "repo": BUNDLE.change.repo,
        "base_sha": "a" * 40,
        "head_sha": HEAD,
    }


def test_code_edges_keep_their_confidence() -> None:
    view = build_graph_view(_bundle(), [])
    assert _edges(view, "IMPORTS") == [(LOANS, FEES, True, "resolved")]
    assert _edges(view, "CALLS") == [
        (CHARGE, LOANS, False, "uncertain"),
        (RETURN_BOOK, CHARGE, True, "resolved"),
    ]


def test_requirement_has_its_criteria() -> None:
    derived = CRITERION.model_copy(
        update={"criterion_id": "SBX-2:v2:d1", "derived_from_description": True}
    )
    view = build_graph_view(_bundle(criteria=[CRITERION, derived]), [])
    assert _edges(view, "HAS_CRITERION") == [
        ("req:SBX-2", "crit:SBX-2:v2:ac1", True, "acceptance_criteria"),
        ("req:SBX-2", "crit:SBX-2:v2:d1", True, "derived_from_description"),
    ]


def test_changes_edges_carry_the_change_kind_and_evidence() -> None:
    second = CHANGED.model_copy(
        update={"evidence_id": "analysis:changed:library/loans.py:25", "change": "added"}
    )
    view = build_graph_view(_bundle(changed=[CHANGED, second]), [])
    (edge,) = [e for e in view.edges if e.kind == "CHANGES"]
    assert (edge.src, edge.dst, edge.resolution_status) == (PR, RETURN_BOOK, "added,modified")
    assert edge.evidence_refs == [
        "analysis:changed:library/loans.py:12",
        "analysis:changed:library/loans.py:25",
    ]


def test_trace_edges_keep_the_link_state_and_only_firm_links_are_confident() -> None:
    links = [
        _link("library.loans.return_book", "OBSERVED"),
        _link("library.fees.charge", "DETERMINISTICALLY_DERIVED"),
        _link("library.fees.charge", "SEMANTIC_CANDIDATE", "SBX-2:v2:ac1"),
        _link("library.loans", "CONFLICTING"),
        _link("library.fees", "UNRESOLVED"),
    ]
    view = build_graph_view(_bundle(), links)
    assert sorted(_edges(view, "TRACE")) == sorted(
        [
            ("req:SBX-2", RETURN_BOOK, True, "observed"),
            ("req:SBX-2", CHARGE, True, "deterministically_derived"),
            ("crit:SBX-2:v2:ac1", CHARGE, False, "semantic_candidate"),
            ("req:SBX-2", LOANS, False, "conflicting"),
            ("req:SBX-2", FEES, False, "unresolved"),
        ]
    )
    observed = next(e for e in view.edges if e.resolution_status == "observed")
    assert observed.evidence_refs == ["evidence:observed"]


def test_trace_edges_come_only_from_links_never_from_calls_or_imports() -> None:
    with_code_edges = build_graph_view(_bundle(), bundle_links(_bundle()))
    without = build_graph_view(_bundle(edges=[]), bundle_links(_bundle(edges=[])))
    assert _edges(with_code_edges, "TRACE") == _edges(without, "TRACE")
    assert build_graph_view(_bundle(), [])  # no links given ...
    assert _edges(build_graph_view(_bundle(), []), "TRACE") == []  # ... no TRACE edges
    assert NO_INFERENCE in with_code_edges.limitations


def test_complete_bundle_is_available_and_external_imports_are_left_out() -> None:
    view = build_graph_view(_bundle(), [])
    assert view.status == "available"
    assert all(e.dst != "dataclasses" and "dataclasses" not in e.dst for e in view.edges)
    assert "1 IMPORTS edge(s) to modules outside the repository are not drawn." in view.limitations
    assert STATIC_EDGES in view.limitations


def test_legacy_bundle_without_calls_extraction_is_incomplete() -> None:
    view = build_graph_view(_bundle(extractor_versions={"jira": "0.1.0", "code": "code@0.1.0"}), [])
    assert view.status == "incomplete"
    assert LEGACY_NO_CALLS in view.limitations


def test_unmapped_hunk_makes_the_graph_incomplete() -> None:
    unmapped = ChangedEntity(
        **evidence("analysis:changed:library/loans.py:1", "analysis"),
        entity_id=None,
        change="unresolved",
        file="library/loans.py",
        hunk_lines=(1, 2),
        commit_shas=[],
    )
    view = build_graph_view(_bundle(changed=[CHANGED, unmapped]), [])
    assert view.status == "incomplete"
    assert "1 changed hunk(s) could not be mapped to a code entity." in view.limitations


def test_edges_to_unknown_entities_are_dropped_not_invented() -> None:
    edges = [
        *EDGES,
        CodeEdge(src="library.fees.charge", dst="gone.f", kind="CALLS", confident=True),
    ]
    view = build_graph_view(_bundle(edges=edges), [_link("library.missing", "OBSERVED")])
    ids = {n.id for n in view.nodes}
    assert all(e.src in ids and e.dst in ids for e in view.edges)
    assert not any("missing" in i or "gone" in i for i in ids)
    assert view.status == "incomplete"
    assert (
        "1 CALLS edge(s) dropped: an end is not a code entity in this bundle." in view.limitations
    )
    assert (
        "1 TRACE edge(s) dropped: an end is not a code entity in this bundle." in view.limitations
    )


def test_bundle_without_code_is_unavailable() -> None:
    view = build_graph_view(_bundle(entities=[], edges=[], changed=[]), [])
    assert view.status == "unavailable"
    assert {n.kind for n in view.nodes} == {"change", "requirement", "criterion"}


def test_deleted_file_entities_are_at_the_merge_base() -> None:
    deleted = CHANGED.model_copy(update={"change": "deleted"})
    view = build_graph_view(_bundle(changed=[deleted]), [])
    node = next(n for n in view.nodes if n.entity_id == "library.loans.return_book")
    assert node.id == f"code:library.loans.return_book@{MERGE_BASE}"
    assert node.revision == MERGE_BASE
    (edge,) = [e for e in view.edges if e.kind == "CHANGES"]
    assert (edge.dst, edge.resolution_status) == (node.id, "deleted")


def test_output_is_deterministic_and_round_trips() -> None:
    links = [_link("library.loans.return_book", "OBSERVED"), _link("library.fees", "UNRESOLVED")]
    first = build_graph_view(_bundle(), links)
    shuffled = build_graph_view(
        _bundle(entities=ENTITIES[::-1], edges=EDGES[::-1]), list(reversed(links))
    )
    assert first.model_dump_json() == shuffled.model_dump_json()
    assert GraphView.model_validate_json(first.model_dump_json()) == first
    assert len({e.id for e in first.edges}) == len(first.edges)


def _check_fixture(name: str, view: GraphView) -> None:
    path = FIXTURES / name
    text = json.dumps(view.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GRAPH_FIXTURES") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == text, f"regenerate {name} (see docs/graph-view.md)"


def test_demo_bundle_matches_its_fixture() -> None:
    bundle = load_bundle(DEMO / DEMO_HASH)
    view = build_graph_view(bundle, bundle_links(bundle))
    assert view.status == "incomplete"  # demo bundles predate CALLS extraction
    _check_fixture("demo_legacy.json", view)


@pytest.mark.parametrize("name", ["sample_with_calls.json"])
def test_sample_bundle_with_every_edge_kind_matches_its_fixture(name: str) -> None:
    second = CHANGED.model_copy(
        update={
            "evidence_id": "analysis:changed:library/fees.py:4",
            "entity_id": "library.fees.charge",
            "file": "library/fees.py",
            "hunk_lines": (4, 6),
        }
    )
    links = [
        _link("library.loans.return_book", "OBSERVED"),
        _link("library.fees.charge", "SEMANTIC_CANDIDATE", "SBX-2:v2:ac1"),
    ]
    view = build_graph_view(_bundle(changed=[CHANGED, second]), links)
    assert view.status == "available"
    assert {e.kind for e in view.edges} == {"CHANGES", "HAS_CRITERION", "TRACE", "IMPORTS", "CALLS"}
    _check_fixture(name, view)
