"""Project a frozen evidence bundle onto the graph-view-v1 contract that the UI draws.

The projection shows what the bundle already holds and adds nothing. Every node and edge comes
from one bundle field or from the trace links computed from the bundle (`bundle_links`):

Nodes:
- `change`: the pull request (`bundle.change`).
- `requirement`: work-item keys, requirement versions, criteria and links.
- `criterion`: `bundle.criteria`.
- `module`, `class`, `function`, `method`: `bundle.entities`, each at the commit its file was
  read from.

Edges (`confident`; `resolution_status`):
- `IMPORTS`, `CALLS` from `bundle.edges`: as extracted; `resolved` or `uncertain`.
- `HAS_CRITERION` from `bundle.criteria`: true; `acceptance_criteria` or
  `derived_from_description`.
- `CHANGES` from `bundle.changed`: true; `added`, `modified` or `deleted` (sorted and joined
  with `,` when one entity has more than one).
- `TRACE` from the trace links: true only for observed or deterministically derived links;
  the link state in lower case.

Rules:
- Nothing is inferred. In particular no TRACE edge is built from CALLS or IMPORTS edges; trace
  links come only from `bundle_links`, which the rules also use.
- An edge whose end is not a node is dropped and counted in `limitations`, never drawn to an
  invented node. Imports of modules outside the repository (`dataclasses`) are left out the same
  way, but they are expected, so they do not make the graph incomplete.
- The same bundle and links always give the same bytes: ids are content hashes and every list
  is sorted.

Status:
- `unavailable`: the bundle has no code entities, so there is no code graph to draw.
- `incomplete`: something the graph needs is missing (no CALLS extraction in the bundle,
  changed hunks that map to no entity, or dropped edges). Each reason is a `limitations` line.
- `available`: none of the above.
"""

from collections.abc import Iterable
from typing import Literal

from codeatlas.bundle.hashing import sha256_hex
from codeatlas.schema import EvidenceBundle, TraceLink
from codeatlas.schema.integration import GraphEdge, GraphNode, GraphSnapshot, GraphView

STATIC_EDGES = "Static edges do not prove execution."
NO_INFERENCE = "CALLS and IMPORTS edges are never used to create trace links."
NO_EDGE_SPANS = (
    "IMPORTS and CALLS edges carry no source span; their provenance is the extractor version "
    "recorded in the bundle."
)
LEGACY_NO_CALLS = "This bundle was collected without CALLS extraction, so it has no CALLS edges."

_CONFIDENT_STATES = frozenset({"OBSERVED", "DETERMINISTICALLY_DERIVED"})
_CODE_KINDS = ("module", "class", "function", "method")
_NODE_ORDER = {
    kind: i for i, kind in enumerate(("change", "requirement", "criterion", *_CODE_KINDS))
}
_EDGE_ORDER = {
    kind: i for i, kind in enumerate(("CHANGES", "HAS_CRITERION", "TRACE", "IMPORTS", "CALLS"))
}


def build_graph_view(bundle: EvidenceBundle, links: Iterable[TraceLink]) -> GraphView:
    """The graph-view-v1 projection of `bundle`, with `links` = `bundle_links(bundle)`."""
    change = bundle.change
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    dropped: dict[str, int] = {}
    limitations: list[str] = [STATIC_EDGES, NO_INFERENCE]

    change_id = f"change:{change.repo}#{change.pr_number}@{change.head_sha}"
    nodes[change_id] = GraphNode(id=change_id, kind="change", revision=change.head_sha)

    # Code entities, each at the revision its file was read from.
    revisions = _file_revisions(bundle)
    code: dict[str, str] = {}  # entity id -> node id
    for entity in bundle.entities:
        revision = revisions.get(entity.file, change.head_sha)
        node_id = f"code:{entity.entity_id}@{revision}"
        code[entity.entity_id] = node_id
        nodes[node_id] = GraphNode(
            id=node_id,
            kind=entity.kind,
            entity_id=entity.entity_id,
            file=entity.file,
            start_line=entity.start_line,
            end_line=entity.end_line,
            revision=revision,
        )

    # Requirements and their criteria.
    links = list(links)
    keys = (
        {ref.key for ref in bundle.work_items}
        | {v.key for v in bundle.versions}
        | {c.key for c in bundle.criteria}
        | {link.requirement_key for link in links}
    )
    for key in keys:
        nodes[_requirement_id(key)] = GraphNode(id=_requirement_id(key), kind="requirement")
    criteria: set[str] = set()
    for criterion in bundle.criteria:
        node_id = _criterion_id(criterion.criterion_id)
        criteria.add(criterion.criterion_id)
        nodes[node_id] = GraphNode(id=node_id, kind="criterion")
        status = (
            "derived_from_description"
            if criterion.derived_from_description
            else "acceptance_criteria"
        )
        _add(edges, "HAS_CRITERION", _requirement_id(criterion.key), node_id, True, status, [])

    # What the pull request changed.
    unmapped = 0
    changes: dict[str, tuple[set[str], set[str]]] = {}  # entity id -> (change kinds, evidence)
    for item in bundle.changed:
        if item.entity_id is None or item.change == "unresolved":
            unmapped += 1
            continue
        kinds, evidence = changes.setdefault(item.entity_id, (set(), set()))
        kinds.add(item.change)
        evidence.add(item.evidence_id)
    for entity_id in sorted(changes):
        kinds, evidence = changes[entity_id]
        if entity_id not in code:
            dropped["CHANGES"] = dropped.get("CHANGES", 0) + 1
            continue
        status = ",".join(sorted(kinds))
        _add(edges, "CHANGES", change_id, code[entity_id], True, status, sorted(evidence))

    # Trace links, exactly as the rules see them.
    for link in links:
        if link.entity_id not in code:
            dropped["TRACE"] = dropped.get("TRACE", 0) + 1
            continue
        if link.criterion_id is not None and link.criterion_id in criteria:
            src = _criterion_id(link.criterion_id)
        else:
            src = _requirement_id(link.requirement_key)
        _add(
            edges,
            "TRACE",
            src,
            code[link.entity_id],
            link.state in _CONFIDENT_STATES,
            link.state.lower(),
            sorted(link.evidence_ids),
        )

    # Static code edges. An import of a module outside the repository (`dataclasses`) has no
    # entity at its end by design: it is left out and counted, but the graph is not incomplete.
    external = 0
    for edge in bundle.edges:
        if edge.kind == "IMPORTS" and edge.src in code and edge.dst not in code:
            external += 1
            continue
        if edge.src not in code or edge.dst not in code:
            dropped[edge.kind] = dropped.get(edge.kind, 0) + 1
            continue
        status = "resolved" if edge.confident else "uncertain"
        _add(edges, edge.kind, code[edge.src], code[edge.dst], edge.confident, status, [])

    if bundle.edges:
        limitations.append(NO_EDGE_SPANS)
    if external:
        limitations.append(
            f"{external} IMPORTS edge(s) to modules outside the repository are not drawn."
        )
    incomplete = False
    if bundle.entities and "calls" not in bundle.extractor_versions:
        limitations.append(LEGACY_NO_CALLS)
        incomplete = True
    if unmapped:
        limitations.append(f"{unmapped} changed hunk(s) could not be mapped to a code entity.")
        incomplete = True
    for kind in sorted(dropped):
        limitations.append(
            f"{dropped[kind]} {kind} edge(s) dropped: an end is not a code entity in this bundle."
        )
        incomplete = True

    status_value: Literal["available", "incomplete", "unavailable"]
    if not bundle.entities:
        status_value = "unavailable"
        limitations.append("The bundle has no code entities, so there is no code graph.")
    else:
        status_value = "incomplete" if incomplete else "available"

    return GraphView(
        status=status_value,
        snapshot=GraphSnapshot(
            repo=change.repo, base_sha=change.base_sha, head_sha=change.head_sha
        ),
        nodes=sorted(nodes.values(), key=lambda n: (_NODE_ORDER.get(n.kind, 99), n.id)),
        edges=sorted(
            edges.values(), key=lambda e: (_EDGE_ORDER.get(e.kind, 99), e.src, e.dst, e.id)
        ),
        limitations=limitations,
    )


def _file_revisions(bundle: EvidenceBundle) -> dict[str, str]:
    """The commit each changed file was parsed at: merge base for deleted files, else head.

    This mirrors `pipeline._code`, which reads a file at the merge base only when every hunk in
    it is a deletion. Files the pull request did not touch are read at head.
    """
    kinds: dict[str, set[str]] = {}
    for item in bundle.changed:
        kinds.setdefault(item.file, set()).add(item.change)
    return {
        path: bundle.change.merge_base_sha
        for path, seen in kinds.items()
        if "deleted" in seen and not seen & {"added", "modified"}
    }


def _requirement_id(key: str) -> str:
    return f"req:{key}"


def _criterion_id(criterion_id: str) -> str:
    return f"crit:{criterion_id}"


def _add(
    edges: dict[str, GraphEdge],
    kind: str,
    src: str,
    dst: str,
    confident: bool,
    status: str,
    evidence: list[str],
) -> None:
    """Add one edge; the same kind, ends and status give the same id, so repeats merge."""
    edge_id = "edge:" + sha256_hex(f"{kind}\0{src}\0{dst}\0{status}".encode())
    old = edges.get(edge_id)
    if old is not None:
        evidence = sorted(set(old.evidence_refs) | set(evidence))
    edges[edge_id] = GraphEdge(
        id=edge_id,
        src=src,
        dst=dst,
        kind=kind,
        confident=confident,
        resolution_status=status,
        evidence_refs=evidence,
    )
