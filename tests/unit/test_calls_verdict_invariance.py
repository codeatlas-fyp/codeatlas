"""CALLS edges must not change any rule outcome or trace link (SG-04).

`evaluate/links.py` and the rules read IMPORTS edges only. These tests prove it on the stored
demo bundles (real pipeline output with PASS, REVIEW and FAIL verdicts): adding every possible
CALLS edge, confident and unconfident, leaves the links, every rule result and the verdict the
same. If a rule starts reading CALLS edges, this test fails and the change has to be reviewed
as a rule change.
"""

from itertools import permutations
from pathlib import Path

import pytest

from codeatlas.bundle.store import load_bundle
from codeatlas.evaluate.links import assign_link_states, bundle_links
from codeatlas.pipeline import evaluate_bundle
from codeatlas.schema import CodeEdge, EvidenceBundle
from tests.samples import CHANGED, GOVERNANCE, WORK_ITEM

DEMO_BUNDLES = sorted(
    p.parent for p in (Path(__file__).parents[2] / "deploy" / "bundles").glob("*/bundle.json")
)


def _with_calls(bundle: EvidenceBundle, confident: bool) -> EvidenceBundle:
    """The bundle with a CALLS edge between every ordered pair of its entities."""
    ids = sorted(e.entity_id for e in bundle.entities)
    calls = [
        CodeEdge(src=s, dst=d, kind="CALLS", confident=confident) for s, d in permutations(ids, 2)
    ]
    others = [e for e in bundle.edges if e.kind != "CALLS"]
    return bundle.model_copy(update={"edges": others + calls})


def _without_calls(bundle: EvidenceBundle) -> EvidenceBundle:
    return bundle.model_copy(update={"edges": [e for e in bundle.edges if e.kind != "CALLS"]})


def _outcomes(bundle: EvidenceBundle, folder: Path) -> tuple[object, ...]:
    verdict = evaluate_bundle(bundle, folder.name)
    results = tuple(sorted((r.case, r.outcome, r.severity, r.message) for r in verdict.results))
    links = tuple(sorted(link.model_dump_json() for link in bundle_links(bundle)))
    return verdict.value, results, links


def test_demo_bundles_are_present() -> None:
    assert len(DEMO_BUNDLES) >= 3


@pytest.mark.parametrize("folder", DEMO_BUNDLES, ids=lambda p: p.name[:12])
def test_calls_edges_do_not_change_links_or_verdict(folder: Path) -> None:
    bundle = load_bundle(folder)
    base = _outcomes(_without_calls(bundle), folder)
    assert _outcomes(bundle, folder) == base
    assert _outcomes(_with_calls(bundle, confident=True), folder) == base
    assert _outcomes(_with_calls(bundle, confident=False), folder) == base


def _two_module_links(kind: str) -> dict[str, str]:
    """Link state per changed entity: `loans` is touched by an SBX-2 commit, `fees` is not,
    and one confident edge of `kind` joins the two modules."""
    keyed, plain = "d" * 40, "f" * 40
    changed = [
        CHANGED,  # library.loans.return_book, touched by the keyed commit
        CHANGED.model_copy(
            update={
                "evidence_id": "analysis:changed:library/fees.py:3",
                "entity_id": "library.fees.charge",
                "file": "library/fees.py",
                "commit_shas": [plain],
            }
        ),
    ]
    links = assign_link_states(
        requirement_keys=["SBX-2"],
        changed=changed,
        commits=[],
        work_item_refs=[WORK_ITEM.model_copy(update={"commit_sha": keyed})],
        edges=[CodeEdge(src="library.loans", dst="library.fees", kind=kind, confident=True)],
        governance=GOVERNANCE.model_copy(update={"adrs": []}),
        candidates=[],
    )
    return {link.entity_id: link.state for link in links}


def test_imports_edge_derives_a_link_but_the_same_calls_edge_does_not() -> None:
    # Control: this is exactly where an edge matters, so the comparison below is not vacuous.
    assert _two_module_links("IMPORTS")["library.fees.charge"] == "DETERMINISTICALLY_DERIVED"
    assert _two_module_links("CALLS")["library.fees.charge"] != "DETERMINISTICALLY_DERIVED"
    assert _two_module_links("CALLS")["library.loans.return_book"] == "OBSERVED"
