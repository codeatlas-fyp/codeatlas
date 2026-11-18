"""Tests for `analyze.calls`: intra-file and cross-file CALLS edges."""

from pathlib import Path

import pytest

try:
    import tree_sitter  # noqa: F401
    import tree_sitter_python  # noqa: F401

    _HAS_TREE_SITTER = True
except ImportError:  # pragma: no cover
    _HAS_TREE_SITTER = False

from codeatlas.analyze.calls import build_call_graph
from codeatlas.analyze.code import extract_entities

pytestmark = pytest.mark.skipif(not _HAS_TREE_SITTER, reason="tree-sitter not installed")

MINI = Path(__file__).parents[1] / "fixtures" / "repos" / "mini_sandbox"


def _graph(files: dict[str, str]) -> set[tuple[str, str, bool]]:
    sources = {path: text.encode() for path, text in files.items()}
    entities = [
        e for path, src in sources.items() for e in extract_entities(file_path=path, source=src)
    ]
    edges = build_call_graph(entities=entities, file_sources=sources)
    assert all(e.kind == "CALLS" for e in edges)
    return {(e.src, e.dst, e.confident) for e in edges}


def test_intra_file_function_call() -> None:
    edges = _graph(
        {"fees.py": ("def rate():\n    return 1\n\ndef fee(days):\n    return days * rate()\n")}
    )
    assert edges == {("fees.fee", "fees.rate", True)}


def test_cross_file_from_import_call() -> None:
    edges = _graph(
        {
            "pkg/fees.py": "def calc(d):\n    return d\n",
            "pkg/loans.py": "from pkg.fees import calc\n\ndef close(d):\n    return calc(d)\n",
        }
    )
    assert ("pkg.loans.close", "pkg.fees.calc", True) in edges


def test_cross_file_aliased_module_call() -> None:
    edges = _graph(
        {
            "pkg/fees.py": "def calc(d):\n    return d\n",
            "pkg/loans.py": "import pkg.fees as f\n\ndef close(d):\n    return f.calc(d)\n",
        }
    )
    assert ("pkg.loans.close", "pkg.fees.calc", True) in edges


def test_relative_import_resolves_to_sibling_module() -> None:
    edges = _graph(
        {
            "pkg/sub/util.py": "def helper():\n    pass\n",
            "pkg/sub/mod.py": "from .util import helper\n\ndef run():\n    helper()\n",
        }
    )
    assert ("pkg.sub.mod.run", "pkg.sub.util.helper", True) in edges


def test_self_method_call_and_base_class_method() -> None:
    edges = _graph(
        {
            "base.py": "class Base:\n    def save(self):\n        pass\n",
            "child.py": (
                "from base import Base\n\n"
                "class Child(Base):\n"
                "    def validate(self):\n        pass\n\n"
                "    def submit(self):\n        self.validate()\n        self.save()\n"
            ),
        }
    )
    assert ("child.Child.submit", "child.Child.validate", True) in edges
    assert ("child.Child.submit", "base.Base.save", True) in edges


def test_calling_a_class_points_at_its_init() -> None:
    edges = _graph(
        {
            "books.py": "class Book:\n    def __init__(self, t):\n        self.t = t\n",
            "shop.py": "from books import Book\n\ndef make():\n    return Book('x')\n",
        }
    )
    assert ("shop.make", "books.Book.__init__", True) in edges


def test_package_reexport_is_followed() -> None:
    edges = _graph(
        {
            "payments/__init__.py": "from .fees import calc\n",
            "payments/fees.py": "def calc():\n    pass\n",
            "app.py": "from payments import calc\n\ndef go():\n    calc()\n",
        }
    )
    assert ("app.go", "payments.fees.calc", True) in edges


def test_external_and_builtin_calls_are_dropped() -> None:
    edges = _graph(
        {
            "a.py": (
                "import json\n\n"
                "class Store:\n    def dumps(self):\n        pass\n\n"
                "def f(x):\n    print(len(x))\n    return json.dumps(x)\n"
            )
        }
    )
    # json.dumps must not be guessed as Store.dumps: `json` is a bound import.
    assert edges == set()


def test_unknown_receiver_unique_method_is_not_confident() -> None:
    edges = _graph(
        {
            "loans.py": "class Loan:\n    def mark_returned(self):\n        pass\n",
            "desk.py": "def close(loan):\n    loan.mark_returned()\n",
        }
    )
    assert edges == {("desk.close", "loans.Loan.mark_returned", False)}


def test_unknown_receiver_with_builtin_method_name_gives_no_edge() -> None:
    edges = _graph(
        {
            "store.py": "class Store:\n    def get(self, k):\n        pass\n",
            "use.py": "def read(d):\n    return d.get('k')\n",
        }
    )
    assert edges == set()


def test_unknown_receiver_ambiguous_method_gives_no_edge() -> None:
    edges = _graph(
        {
            "a.py": "class A:\n    def save(self):\n        pass\n",
            "b.py": "class B:\n    def save(self):\n        pass\n",
            "c.py": "def go(x):\n    x.save()\n",
        }
    )
    assert edges == set()


def test_module_level_call_has_module_as_source_and_recursion_dropped() -> None:
    edges = _graph(
        {
            "m.py": (
                "def setup():\n    pass\n\n"
                "def fact(n):\n    return 1 if n < 2 else n * fact(n - 1)\n\n"
                "setup()\n"
            )
        }
    )
    assert edges == {("m", "m.setup", True)}


def test_nested_function_calls_credited_to_enclosing_function() -> None:
    edges = _graph(
        {
            "m.py": (
                "def helper():\n    pass\n\n"
                "def outer():\n    def inner():\n        helper()\n    inner()\n"
            )
        }
    )
    assert ("m.outer", "m.helper", True) in edges


def test_output_is_sorted_and_deduplicated() -> None:
    sources = {"m.py": b"def a():\n    pass\n\ndef b():\n    a()\n    a()\n\ndef c():\n    b()\n"}
    entities = extract_entities(file_path="m.py", source=sources["m.py"])
    edges = build_call_graph(entities=entities, file_sources=sources)
    pairs = [(e.src, e.dst) for e in edges]
    assert pairs == sorted(set(pairs)) == [("m.b", "m.a"), ("m.c", "m.b")]


def test_mini_sandbox_fixture_has_cross_file_edges() -> None:
    sources = {p.relative_to(MINI).as_posix(): p.read_bytes() for p in sorted(MINI.rglob("*.py"))}
    entities = [
        e for path, src in sources.items() for e in extract_entities(file_path=path, source=src)
    ]
    edges = build_call_graph(entities=entities, file_sources=sources)
    ids = {e.entity_id for e in entities}
    assert all(e.src in ids and e.dst in ids for e in edges)


# ---- local scope (handover gaps G1 to G3) ---------------------------------


def test_parameter_shadows_global_function_gives_no_edge() -> None:
    edges = _graph({"m.py": "def target():\n    pass\n\ndef caller(target):\n    target()\n"})
    assert edges == set()


def test_star_and_keyword_parameters_shadow_globals() -> None:
    edges = _graph(
        {
            "m.py": (
                "def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n\n"
                "def caller(x, *a, k: int = 1, b=2, **c):\n    a()\n    b()\n    c()\n"
            )
        }
    )
    assert edges == set()


def test_local_assignment_shadows_global_function() -> None:
    edges = _graph(
        {"m.py": "def target():\n    pass\n\ndef local():\n    target = print\n    target()\n"}
    )
    assert edges == set()


def test_loop_with_except_and_walrus_targets_shadow_globals() -> None:
    edges = _graph(
        {
            "m.py": (
                "def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n\n"
                "def d():\n    pass\n\n"
                "def f(items):\n"
                "    for a in items:\n        a()\n"
                "    with open('x') as b:\n        b()\n"
                "    try:\n        pass\n    except Exception as c:\n        c()\n"
                "    if (d := items):\n        d()\n"
            )
        }
    )
    assert edges == set()


def test_assignment_in_tuple_target_shadows_global() -> None:
    edges = _graph(
        {
            "m.py": (
                "def target():\n    pass\n\n"
                "def f():\n    x, (y, target) = 1, (2, 3)\n    target()\n"
            )
        }
    )
    assert edges == set()


def test_global_statement_keeps_module_binding() -> None:
    edges = _graph(
        {"m.py": "def target():\n    pass\n\ndef f():\n    global target\n    target()\n"}
    )
    assert edges == {("m.f", "m.target", True)}


def test_attribute_assignment_does_not_shadow() -> None:
    edges = _graph(
        {"m.py": "def target():\n    pass\n\ndef f(obj):\n    obj.target = 1\n    target()\n"}
    )
    assert edges == {("m.f", "m.target", True)}


def test_nested_function_name_shadows_global() -> None:
    edges = _graph(
        {
            "m.py": (
                "def helper():\n    pass\n\n"
                "def outer():\n    def helper():\n        pass\n    helper()\n"
            )
        }
    )
    assert edges == set()


def test_closure_sees_enclosing_parameter() -> None:
    edges = _graph(
        {
            "m.py": (
                "def target():\n    pass\n\n"
                "def outer(target):\n    def inner():\n        target()\n    inner()\n"
            )
        }
    )
    assert edges == set()


def test_function_local_import_does_not_leak_to_other_functions() -> None:
    edges = _graph(
        {
            "pkg/b.py": "def helper():\n    pass\n",
            "pkg/a.py": (
                "def f():\n    from pkg.b import helper\n    helper()\n\ndef g():\n    helper()\n"
            ),
        }
    )
    assert edges == {("pkg.a.f", "pkg.b.helper", True)}


def test_function_local_import_resolves_inside_nested_function() -> None:
    edges = _graph(
        {
            "pkg/b.py": "def helper():\n    pass\n",
            "pkg/a.py": (
                "def f():\n    import pkg.b as bee\n"
                "    def inner():\n        bee.helper()\n    inner()\n"
            ),
        }
    )
    assert edges == {("pkg.a.f", "pkg.b.helper", True)}


def test_local_import_shadows_module_level_name() -> None:
    edges = _graph(
        {
            "pkg/b.py": "def helper():\n    pass\n",
            "pkg/c.py": "def helper():\n    pass\n",
            "pkg/a.py": (
                "from pkg.c import helper\n\n"
                "def f():\n    from pkg.b import helper\n    helper()\n\n"
                "def g():\n    helper()\n"
            ),
        }
    )
    assert edges == {("pkg.a.f", "pkg.b.helper", True), ("pkg.a.g", "pkg.c.helper", True)}


def test_parameter_receiver_still_uses_unique_method_guess() -> None:
    edges = _graph(
        {
            "m.py": (
                "class Repo:\n    def persist(self):\n        pass\n\n"
                "def save_all(repo):\n    repo.persist()\n"
            )
        }
    )
    assert edges == {("m.save_all", "m.Repo.persist", False)}
