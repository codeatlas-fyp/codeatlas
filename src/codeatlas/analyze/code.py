"""Code analysis for Python sources (spec §6 `analyze.code`, step-11).

Three jobs, all pure functions over already-collected text:

1. **Entity extraction** — parse a Python file with tree-sitter, emit one
   `CodeEntity` per module/class/function/method with (file, start_line,
   end_line, signature, docstring).

2. **Hunk → entity mapping** — given diff hunks and the head-side file text,
   decide which entity each hunk falls inside. If no entity encloses the hunk
   lines (e.g. a module-level constant edit, a renamed function, a config file
   change), the resulting `ChangedEntity` has `entity_id=None` and
   `change="unresolved"`. **An unresolved mapping is never a failure.** Rules
   C1–C5 don't need function-level resolution; C9 is designed so unresolved
   entities fall through to review, not block.

3. **Module import graph** — walks each entity's AST for `import` /
   `from … import …` statements and emits `CodeEdge(src, dst, kind="IMPORTS",
   confident=True)`. CALLS edges are deferred to Iteration 2 (D-table D10:
   "direct call edges deferred to Iteration 2").

All functions pure. No file I/O — the caller passes file bytes from
`collect.git.LocalRepo.file_bytes(sha, path)`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from codeatlas.schema import ChangedEntity, CodeEdge, CodeEntity

if TYPE_CHECKING:
    import tree_sitter

EXTRACTOR_VERSION = "code@0.1.0"
_PY_MODULE_SUFFIXES = (".py",)


# ---- entity extraction ---------------------------------------------------

@dataclass(frozen=True)
class _ParsedEntity:
    """Working-set entity before it's turned into a schema CodeEntity.

    Separate dataclass so we don't need to construct schema objects every time
    the AST walker touches a node — only materialise once at the top.
    """

    name: str
    kind: str
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None
    children: tuple["_ParsedEntity", ...]


def _tree_sitter_language() -> "tree_sitter.Language":
    """Load the Python grammar. Import inline so tests without tree-sitter can skip."""
    import tree_sitter
    import tree_sitter_python

    return tree_sitter.Language(tree_sitter_python.language())


def _parser() -> "tree_sitter.Parser":
    import tree_sitter

    return tree_sitter.Parser(_tree_sitter_language())


def extract_entities(
    *,
    file_path: str,
    source: bytes,
    module_prefix: str = "",
) -> list[CodeEntity]:
    """Return every structural entity in a Python source file.

    `module_prefix` is prepended to the module name — pass `pkg` to get entities
    like `pkg.payments.fees.calculate_late_fee` instead of
    `payments.fees.calculate_late_fee`. Default empty for repo-relative paths.
    """
    try:
        parser = _parser()
    except ImportError:
        # tree-sitter not installed — degrade to a crude heuristic that still
        # produces a module-level entity so downstream code works.
        return _fallback_module_only_entity(file_path, source)

    tree = parser.parse(source)
    if tree.root_node is None:
        return _fallback_module_only_entity(file_path, source)

    module_name = _module_name_from_path(file_path, module_prefix)
    module_entity = CodeEntity(
        entity_id=module_name,
        kind="module",
        file=file_path,
        start_line=1,
        end_line=max(1, source.count(b"\n") + 1),
        signature=None,
        docstring=_module_docstring(tree.root_node, source),
    )

    nested = _walk(
        node=tree.root_node,
        source=source,
        file_path=file_path,
        qualifier=module_name,
    )

    return [module_entity, *nested]


def _walk(
    *,
    node: "tree_sitter.Node",
    source: bytes,
    file_path: str,
    qualifier: str,
) -> list[CodeEntity]:
    """Depth-first walk emitting entities for every class/function/method."""
    out: list[CodeEntity] = []
    for child in node.children:
        if child.type == "class_definition":
            name = _identifier(child, source)
            if name is None:
                continue
            fqn = f"{qualifier}.{name}"
            out.append(
                CodeEntity(
                    entity_id=fqn,
                    kind="class",
                    file=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    signature=None,
                    docstring=_docstring_of(child, source),
                )
            )
            # Methods inside the class.
            body = child.child_by_field_name("body")
            if body is not None:
                out.extend(_walk(node=body, source=source, file_path=file_path, qualifier=fqn))
        elif child.type == "function_definition":
            name = _identifier(child, source)
            if name is None:
                continue
            fqn = f"{qualifier}.{name}"
            # Nested functions inside functions keep walking (rare, but valid).
            kind = "method" if _looks_like_method(qualifier) else "function"
            out.append(
                CodeEntity(
                    entity_id=fqn,
                    kind=kind,
                    file=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    signature=_signature(child, source),
                    docstring=_docstring_of(child, source),
                )
            )
        elif child.type == "decorated_definition":
            # Decorators wrap a function/class — recurse into the wrapped node.
            out.extend(_walk(node=child, source=source, file_path=file_path, qualifier=qualifier))
    return out


def _identifier(node: "tree_sitter.Node", source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is None:
        return None
    return source[name.start_byte:name.end_byte].decode("utf-8", errors="replace")


def _signature(node: "tree_sitter.Node", source: bytes) -> str | None:
    """Return `def name(params) -> ret` as a single line, or None if we can't."""
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    returns_node = node.child_by_field_name("return_type")
    if name_node is None or params_node is None:
        return None
    name = source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
    params = source[params_node.start_byte:params_node.end_byte].decode("utf-8", errors="replace")
    ret = ""
    if returns_node is not None:
        ret_text = source[returns_node.start_byte:returns_node.end_byte].decode(
            "utf-8", errors="replace"
        )
        ret = f" -> {ret_text}"
    return f"def {name}{params}{ret}"


def _docstring_of(node: "tree_sitter.Node", source: bytes) -> str | None:
    body = node.child_by_field_name("body")
    if body is None or len(body.children) == 0:
        return None
    first = body.children[0]
    if first.type != "expression_statement":
        return None
    inner = first.children[0] if first.children else None
    if inner is None or inner.type != "string":
        return None
    text = source[inner.start_byte:inner.end_byte].decode("utf-8", errors="replace")
    return _strip_quotes(text)


def _module_docstring(root: "tree_sitter.Node", source: bytes) -> str | None:
    for child in root.children:
        if child.type == "expression_statement":
            inner = child.children[0] if child.children else None
            if inner is not None and inner.type == "string":
                text = source[inner.start_byte:inner.end_byte].decode(
                    "utf-8", errors="replace"
                )
                return _strip_quotes(text)
        if child.type not in ("comment", "expression_statement"):
            # First non-comment, non-string statement — no module docstring.
            return None
    return None


def _strip_quotes(text: str) -> str:
    text = text.strip()
    for quote in ('"""', "'''", '"', "'"):
        if text.startswith(quote) and text.endswith(quote) and len(text) >= 2 * len(quote):
            return text[len(quote) : -len(quote)].strip()
    return text


def _looks_like_method(qualifier: str) -> bool:
    """A qualifier like `pkg.module.ClassName` means we're inside a class body."""
    tail = qualifier.rsplit(".", 1)[-1]
    return bool(tail) and tail[0].isupper()


def _module_name_from_path(file_path: str, prefix: str) -> str:
    """`payments/fees.py` → `payments.fees`; `books.py` → `books`."""
    path = PurePosixPath(file_path)
    parts = list(path.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    name = ".".join(parts) if parts else path.stem
    if prefix:
        name = f"{prefix}.{name}" if name else prefix
    return name


def _fallback_module_only_entity(file_path: str, source: bytes) -> list[CodeEntity]:
    """When tree-sitter is unavailable, emit a single module entity.

    Spec step 11 acceptance says: "unsupported language → file-level entity,
    not crash". This path takes the same shape for the Python-unavailable case.
    """
    module_name = _module_name_from_path(file_path, "")
    return [
        CodeEntity(
            entity_id=module_name,
            kind="module",
            file=file_path,
            start_line=1,
            end_line=max(1, source.count(b"\n") + 1),
            signature=None,
            docstring=None,
        )
    ]


# ---- hunk → entity mapping ----------------------------------------------

def map_hunks_to_entities(
    *,
    file_path: str,
    entities: list[CodeEntity],
    hunk_lines: list[tuple[int, int]],
    change_type: str,
    commit_shas: list[str],
    retrieved_at: datetime,
) -> list[ChangedEntity]:
    """One ChangedEntity per hunk; `entity_id=None` + `change="unresolved"` on misses.

    "Enclosing entity" means the deepest entity whose [start_line, end_line]
    span contains the hunk's line range. We pick the smallest span to prefer
    methods over their enclosing classes.
    """
    results: list[ChangedEntity] = []
    for start, end in hunk_lines:
        enclosing = _smallest_enclosing(entities, file_path, start, end)
        if enclosing is None:
            results.append(
                ChangedEntity(
                    evidence_id=f"changed:{file_path}:{start}-{end}:unresolved",
                    source="analysis",
                    source_ref=f"diff:{file_path}:{start}-{end}",
                    retrieved_at=retrieved_at,
                    extractor_version=EXTRACTOR_VERSION,
                    entity_id=None,
                    change="unresolved",
                    file=file_path,
                    hunk_lines=(start, end),
                    commit_shas=list(commit_shas),
                )
            )
            continue
        results.append(
            ChangedEntity(
                evidence_id=f"changed:{file_path}:{start}-{end}:{enclosing.entity_id}",
                source="analysis",
                source_ref=f"diff:{file_path}:{start}-{end}",
                retrieved_at=retrieved_at,
                extractor_version=EXTRACTOR_VERSION,
                entity_id=enclosing.entity_id,
                change=_normalise_change(change_type),  # type: ignore[arg-type]
                file=file_path,
                hunk_lines=(start, end),
                commit_shas=list(commit_shas),
            )
        )
    return results


def _smallest_enclosing(
    entities: list[CodeEntity], file_path: str, start: int, end: int
) -> CodeEntity | None:
    """Pick the entity in `file_path` whose span contains [start, end] with the smallest size.

    Modules are included only as a last-resort fallback — if a hunk falls
    outside every class/function, we prefer `entity_id=None` over marking the
    whole module as changed, because "module changed" is not actionable
    evidence for the semantic rules downstream.
    """
    best: CodeEntity | None = None
    best_span: int = 0
    for entity in entities:
        if entity.file != file_path or entity.kind == "module":
            continue
        if entity.start_line <= start and entity.end_line >= end:
            span = entity.end_line - entity.start_line
            if best is None or span < best_span:
                best = entity
                best_span = span
    return best


def _normalise_change(change_type: str) -> str:
    """Translate collector change_type into schema's ChangedEntity.change literal."""
    mapping = {
        "added": "added",
        "modified": "modified",
        "deleted": "deleted",
        "renamed": "modified",  # renamed means the content moved; treat as modified
    }
    return mapping.get(change_type, "modified")


# ---- import graph --------------------------------------------------------

_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+(?:\s*,\s*[.\w]+)*))",
    re.MULTILINE,
)


def build_import_graph(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
    package_prefix: str = "",
) -> list[CodeEdge]:
    """One IMPORTS edge per `import X` or `from X import ...` per module.

    Resolves relative imports (`from . import foo`) against the module's own
    package. The `confident` flag is True because imports are structural and
    text-visible — unlike CALLS which require call-graph analysis we deferred.
    """
    module_entities = [e for e in entities if e.kind == "module"]
    module_lookup = {e.file: e.entity_id for e in module_entities}

    edges: list[CodeEdge] = []
    for module in module_entities:
        source = file_sources.get(module.file)
        if source is None:
            continue
        text = source.decode("utf-8", errors="replace")
        for from_module, bare_imports in _IMPORT_PATTERN.findall(text):
            targets: list[str] = []
            if from_module:
                targets.append(_resolve_import(from_module, module.entity_id, package_prefix))
            if bare_imports:
                for raw in bare_imports.split(","):
                    name = raw.strip()
                    if name:
                        targets.append(_resolve_import(name, module.entity_id, package_prefix))
            for target in targets:
                if target == module.entity_id:
                    continue  # self-reference, ignore
                edges.append(
                    CodeEdge(
                        src=module.entity_id,
                        dst=target,
                        kind="IMPORTS",
                        confident=True,
                    )
                )

    # De-duplicate while preserving order (same import stated twice shows once).
    seen: set[tuple[str, str, str]] = set()
    unique: list[CodeEdge] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def _resolve_import(name: str, importer_module: str, package_prefix: str) -> str:
    """Resolve `.foo` and `..bar` relative imports against the importer's package."""
    if not name.startswith("."):
        return name
    # Count leading dots to determine how many parent packages to pop.
    dots = len(name) - len(name.lstrip("."))
    tail = name[dots:]
    importer_parts = importer_module.split(".")
    # pop `dots` levels; a single '.' means "same package", popping 0 is wrong,
    # so dots-1 is the pop count for from-imports.
    pop = max(0, dots - 1)
    if pop > 0:
        importer_parts = importer_parts[:-pop] if pop <= len(importer_parts) else []
    base = ".".join(importer_parts)
    if tail:
        return f"{base}.{tail}" if base else tail
    return base
