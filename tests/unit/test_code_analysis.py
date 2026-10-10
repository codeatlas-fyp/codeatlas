"""Tests for `analyze.code` — entity extraction, hunk mapping, import graph."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

try:
    import tree_sitter  # noqa: F401
    import tree_sitter_python  # noqa: F401
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False

from codeatlas.analyze.code import (
    build_import_graph,
    extract_entities,
    map_hunks_to_entities,
)

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)

SIMPLE_PY = b'''\
"""Module docstring."""

import os
from pathlib import Path

CONSTANT = 42


def top_level_fn(x: int) -> int:
    """One-line docstring."""
    return x + 1


class Calculator:
    """A calculator class."""

    def __init__(self) -> None:
        self.value = 0

    def add(self, n: int) -> int:
        """Add n to the current value."""
        self.value += n
        return self.value


def _private_helper():
    pass
'''

skipif_no_tree_sitter = pytest.mark.skipif(
    not _HAS_TREE_SITTER, reason="tree-sitter not installed"
)


@skipif_no_tree_sitter
def test_extract_entities_finds_module_and_top_level() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    ids = {e.entity_id: e.kind for e in entities}
    assert "calc" in ids and ids["calc"] == "module"
    assert "calc.top_level_fn" in ids and ids["calc.top_level_fn"] == "function"
    assert "calc.Calculator" in ids and ids["calc.Calculator"] == "class"
    assert "calc._private_helper" in ids


@skipif_no_tree_sitter
def test_extract_entities_finds_methods_as_methods() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    ids = {e.entity_id: e.kind for e in entities}
    assert ids["calc.Calculator.__init__"] == "method"
    assert ids["calc.Calculator.add"] == "method"


@skipif_no_tree_sitter
def test_extract_entities_captures_signatures() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    add_entity = next(e for e in entities if e.entity_id == "calc.Calculator.add")
    assert add_entity.signature is not None
    assert "add(self, n: int)" in add_entity.signature
    assert "-> int" in add_entity.signature


@skipif_no_tree_sitter
def test_extract_entities_captures_docstrings() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    module = next(e for e in entities if e.entity_id == "calc")
    fn = next(e for e in entities if e.entity_id == "calc.top_level_fn")
    assert module.docstring == "Module docstring."
    assert fn.docstring == "One-line docstring."


@skipif_no_tree_sitter
def test_module_prefix_prepended() -> None:
    entities = extract_entities(
        file_path="payments/fees.py", source=SIMPLE_PY, module_prefix="codeatlas",
    )
    assert any(e.entity_id == "codeatlas.payments.fees" for e in entities)
    assert any(e.entity_id == "codeatlas.payments.fees.top_level_fn" for e in entities)


@skipif_no_tree_sitter
def test_hunk_maps_to_enclosing_function() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    # top_level_fn spans roughly lines 9-11; a hunk on line 10 should map to it.
    top_fn = next(e for e in entities if e.entity_id == "calc.top_level_fn")
    changed = map_hunks_to_entities(
        file_path="calc.py",
        entities=entities,
        hunk_lines=[(top_fn.start_line, top_fn.end_line)],
        change_type="modified",
        commit_shas=["abc"],
        retrieved_at=NOW,
    )
    assert len(changed) == 1
    assert changed[0].entity_id == "calc.top_level_fn"
    assert changed[0].change == "modified"


@skipif_no_tree_sitter
def test_hunk_prefers_method_over_enclosing_class() -> None:
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY)
    add_method = next(e for e in entities if e.entity_id == "calc.Calculator.add")
    changed = map_hunks_to_entities(
        file_path="calc.py",
        entities=entities,
        hunk_lines=[(add_method.start_line, add_method.end_line)],
        change_type="modified",
        commit_shas=["abc"],
        retrieved_at=NOW,
    )
    assert changed[0].entity_id == "calc.Calculator.add"  # method, not Calculator


def test_hunk_outside_any_entity_is_unresolved() -> None:
    """A hunk on CONSTANT = 42 falls outside every class/function → unresolved."""
    entities = extract_entities(file_path="calc.py", source=SIMPLE_PY) if _HAS_TREE_SITTER else []
    changed = map_hunks_to_entities(
        file_path="calc.py",
        entities=entities,
        hunk_lines=[(6, 6)],  # the CONSTANT line
        change_type="modified",
        commit_shas=["abc"],
        retrieved_at=NOW,
    )
    assert len(changed) == 1
    assert changed[0].entity_id is None
    assert changed[0].change == "unresolved"


def test_unresolved_mapping_does_not_crash_without_tree_sitter() -> None:
    """Without tree-sitter, every hunk becomes unresolved — never a crash."""
    changed = map_hunks_to_entities(
        file_path="calc.py",
        entities=[],  # no entities = nothing can enclose
        hunk_lines=[(1, 5), (10, 20)],
        change_type="modified",
        commit_shas=["abc"],
        retrieved_at=NOW,
    )
    assert len(changed) == 2
    assert all(c.entity_id is None for c in changed)
    assert all(c.change == "unresolved" for c in changed)


@skipif_no_tree_sitter
def test_build_import_graph_finds_both_import_forms() -> None:
    """`import X` and `from X import Y` both emit IMPORTS edges."""
    source = b'''\
import json
import os.path
from pathlib import Path
from collections import defaultdict, Counter

def f():
    pass
'''
    entities = extract_entities(file_path="demo.py", source=source)
    edges = build_import_graph(entities=entities, file_sources={"demo.py": source})

    dests = {e.dst for e in edges if e.kind == "IMPORTS"}
    assert "json" in dests
    assert "os.path" in dests
    assert "pathlib" in dests  # from X import Y records X as the target
    assert "collections" in dests


@skipif_no_tree_sitter
def test_build_import_graph_deduplicates_identical_edges() -> None:
    source = b'''\
import json
import json
'''
    entities = extract_entities(file_path="demo.py", source=source)
    edges = build_import_graph(entities=entities, file_sources={"demo.py": source})
    assert len([e for e in edges if e.dst == "json"]) == 1


@skipif_no_tree_sitter
def test_build_import_graph_resolves_relative_imports() -> None:
    """Relative imports resolve to the from-package, not imported names."""
    source = b'''\
from . import sibling
from ..parent_pkg import something
from .utils import helper
'''
    entities = extract_entities(
        file_path="pkg/subpkg/mod.py", source=source,
    )
    edges = build_import_graph(
        entities=entities, file_sources={"pkg/subpkg/mod.py": source},
    )
    dests = {e.dst for e in edges if e.kind == "IMPORTS"}
    assert any("subpkg" in d for d in dests), f"expected a dest in subpkg, got {dests}"
    assert any("parent_pkg" in d for d in dests), (
        f"expected a dest with parent_pkg, got {dests}"
    )


@skipif_no_tree_sitter
def test_fallback_when_source_is_unparseable() -> None:
    """Garbled source → tree-sitter still returns a tree; worst case module-only."""
    source = b"this is not valid python {{"
    entities = extract_entities(file_path="broken.py", source=source)
    assert len(entities) >= 1
    assert entities[0].kind == "module"
