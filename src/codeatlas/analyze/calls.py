"""Static call graph for Python sources: intra-file and cross-file CALLS edges.

`build_call_graph` walks every parsed file with tree-sitter and emits one
`CodeEdge(src, dst, kind="CALLS")` per caller/callee pair, where both ends are
entities that `extract_entities` produced (module, class, function, method).
Calls into the standard library or third-party packages are left out, because
they have no entity in the repository.

How a call is resolved, in order:

1. `name()`: a function or class defined in the same module, or a name bound
   by an import (`from pkg.fees import calc`, `import pkg.fees as fees`).
2. `self.m()` / `cls.m()` inside a class: that class's method, then its base
   classes' methods when the bases are repository classes.
3. `alias.m()` / `alias.Class.m()`: the dotted path is rewritten through the
   module's import table, then looked up.
4. Calling a class means calling its `__init__` when it has one, else the class.
5. Names re-exported through a package `__init__` are followed (a few hops).

Edges from steps 1-5 are `confident=True`: the callee is fixed by the source
text. One weaker step remains:

6. `obj.m()` with an unknown receiver: if exactly one method in the whole
   repository is named `m`, emit an edge with `confident=False`. More than one
   candidate means no edge, because a guess would be wrong too often.

Calls made at module level have the module entity as `src`. Calls inside a
nested function are credited to the nearest enclosing entity, since nested
functions are not entities. Self-calls (recursion) are dropped, matching how
`build_import_graph` drops self-imports.

Pure: no file I/O, deterministic output order (sorted by src, dst).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from codeatlas.analyze.code import _parser
from codeatlas.schema import CodeEdge, CodeEntity

if TYPE_CHECKING:
    import tree_sitter

EXTRACTOR_VERSION = "calls@0.1.0"
_MAX_HOPS = 4  # re-export and base-class lookups; stops cycles

# Method names that built-in types already have. `d.get(k)` on a dict must not be guessed as
# the one repository method called `get`, so these names never take the unique-method step.
_BUILTIN_METHOD_NAMES = frozenset(
    name
    for kind in (dict, list, str, bytes, set, frozenset, tuple, int, float, object)
    for name in dir(kind)
) | {"read", "write", "close", "open", "readline", "readlines", "flush", "seek"}


@dataclass
class _Module:
    """What one file tells us about name binding."""

    name: str
    is_package: bool
    names: dict[str, str] = field(default_factory=dict)  # local name -> dotted target
    bases: dict[str, list[str]] = field(default_factory=dict)  # class fqn -> base targets


@dataclass(frozen=True)
class _Call:
    src: str
    expr: str  # dotted callee as written, e.g. "fees.calc" or "self.save"
    enclosing_class: str | None


def build_call_graph(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
) -> list[CodeEdge]:
    """Return CALLS edges between repository entities, sorted by (src, dst)."""
    try:
        parser = _parser()
    except ImportError:
        return []  # no tree-sitter: no call graph, never a crash

    known = {e.entity_id: e for e in entities}
    module_of_file = {e.file: e.entity_id for e in entities if e.kind == "module"}
    methods_by_name: dict[str, list[str]] = {}
    for e in entities:
        if e.kind == "method":
            methods_by_name.setdefault(e.entity_id.rsplit(".", 1)[-1], []).append(e.entity_id)

    modules: dict[str, _Module] = {}
    calls: list[_Call] = []
    for path in sorted(file_sources):
        module_name = module_of_file.get(path)
        if module_name is None or not path.endswith(".py"):
            continue
        tree = parser.parse(file_sources[path])
        if tree.root_node is None:
            continue
        mod = _Module(name=module_name, is_package=PurePosixPath(path).name == "__init__.py")
        source = file_sources[path]
        _collect_bindings(tree.root_node, source, mod)
        modules[module_name] = mod
        _collect_calls(tree.root_node, source, mod, known, module_name, None, calls)

    resolver = _Resolver(known=known, modules=modules, module_of_file=module_of_file)
    edges: set[tuple[str, str, bool]] = set()
    for call in calls:
        dst, bound = resolver.resolve(call)
        confident = True
        if dst is None and not bound:
            dst = _unique_method(call.expr, methods_by_name)
            confident = False
        if dst is None or dst == call.src:
            continue
        edges.add((call.src, dst, confident))

    # A pair seen both confidently and as a guess keeps only the confident edge.
    confident_pairs = {(s, d) for s, d, c in edges if c}
    return [
        CodeEdge(src=s, dst=d, kind="CALLS", confident=c)
        for s, d, c in sorted(edges)
        if c or (s, d) not in confident_pairs
    ]


# ---- bindings ------------------------------------------------------------


def _text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _collect_bindings(root: tree_sitter.Node, source: bytes, mod: _Module) -> None:
    """Fill `mod.names` from top-level defs and every import in the file."""
    for child in root.children:
        node = child
        if node.type == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner is None:
                continue
            node = inner
        if node.type in ("function_definition", "class_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                mod.names[_text(name, source)] = f"{mod.name}.{_text(name, source)}"
    _collect_imports(root, source, mod)


def _collect_imports(node: tree_sitter.Node, source: bytes, mod: _Module) -> None:
    for child in node.children:
        if child.type == "import_statement":
            for item in child.children_by_field_name("name"):
                if item.type == "aliased_import":
                    target = item.child_by_field_name("name")
                    alias = item.child_by_field_name("alias")
                    if target is not None and alias is not None:
                        mod.names[_text(alias, source)] = _text(target, source)
                else:
                    dotted = _text(item, source)
                    head = dotted.split(".", 1)[0]
                    mod.names.setdefault(head, head)  # `import a.b` binds `a`
        elif child.type == "import_from_statement":
            module_node = child.child_by_field_name("module_name")
            if module_node is None:
                continue
            base = _resolve_from(_text(module_node, source), mod)
            for item in child.children_by_field_name("name"):
                if item.type == "aliased_import":
                    target = item.child_by_field_name("name")
                    alias = item.child_by_field_name("alias")
                    if target is not None and alias is not None:
                        mod.names[_text(alias, source)] = _join(base, _text(target, source))
                else:
                    name = _text(item, source)
                    mod.names[name.split(".")[-1]] = _join(base, name)
        elif child.type not in ("string", "comment"):
            _collect_imports(child, source, mod)  # imports inside functions, try blocks


def _resolve_from(module_text: str, mod: _Module) -> str:
    """`from .x import y` inside `pkg.sub.mod` -> `pkg.sub.x`; absolute names pass through."""
    if not module_text.startswith("."):
        return module_text
    dots = len(module_text) - len(module_text.lstrip("."))
    tail = module_text[dots:]
    parts = mod.name.split(".")
    package = parts if mod.is_package else parts[:-1]
    keep = len(package) - (dots - 1)
    base = ".".join(package[: max(0, keep)])
    return _join(base, tail)


def _join(base: str, tail: str) -> str:
    if base and tail:
        return f"{base}.{tail}"
    return base or tail


# ---- calls ---------------------------------------------------------------


def _collect_calls(
    node: tree_sitter.Node,
    source: bytes,
    mod: _Module,
    known: dict[str, CodeEntity],
    current: str,
    enclosing_class: str | None,
    out: list[_Call],
) -> None:
    """Depth-first: track the enclosing entity and record each call under it."""
    for child in node.children:
        if child.type == "class_definition":
            name = child.child_by_field_name("name")
            fqn = f"{current}.{_text(name, source)}" if name else None
            if fqn and fqn in known:
                supers = child.child_by_field_name("superclasses")
                if supers is not None:
                    mod.bases[fqn] = [
                        _text(arg, source)
                        for arg in supers.named_children
                        if arg.type in ("identifier", "attribute")
                    ]
                # Class-body statements (decorators, defaults) are credited to the class.
                _collect_calls(child, source, mod, known, fqn, fqn, out)
            else:
                _collect_calls(child, source, mod, known, current, enclosing_class, out)
            continue
        if child.type == "function_definition":
            name = child.child_by_field_name("name")
            fqn = f"{current}.{_text(name, source)}" if name else None
            if fqn and fqn in known and known[current].kind in ("module", "class"):
                _collect_calls(child, source, mod, known, fqn, enclosing_class, out)
            else:
                _collect_calls(child, source, mod, known, current, enclosing_class, out)
            continue
        if child.type == "call":
            fn = child.child_by_field_name("function")
            expr = _dotted(fn, source) if fn is not None else None
            if expr is not None:
                out.append(_Call(src=current, expr=expr, enclosing_class=enclosing_class))
        _collect_calls(child, source, mod, known, current, enclosing_class, out)


def _dotted(node: tree_sitter.Node, source: bytes) -> str | None:
    """`a.b.c` for identifier/attribute chains; None for `x[0]()`, `f()()`, `super().m()`."""
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is None or attr is None:
            return None
        head = _dotted(obj, source)
        return f"{head}.{_text(attr, source)}" if head is not None else None
    return None


# ---- resolution ----------------------------------------------------------


@dataclass
class _Resolver:
    known: dict[str, CodeEntity]
    modules: dict[str, _Module]
    module_of_file: dict[str, str]

    def resolve(self, call: _Call) -> tuple[str | None, bool]:
        """Return (entity or None, whether the head name is bound in the caller's module).

        A bound head that resolves to nothing is an external call (`json.dumps`) and must not
        fall back to the unique-method guess.
        """
        head, _, rest = call.expr.partition(".")
        mod = self.modules.get(self._module_of(call.src))
        if mod is None:
            return None, False
        if head in ("self", "cls") and call.enclosing_class and rest:
            return self._method(call.enclosing_class, rest, 0), "." not in rest
        target = mod.names.get(head)
        if target is None:
            return None, False
        return self._entity(_join(target, rest), 0), True

    def _module_of(self, entity_id: str) -> str:
        return self.module_of_file.get(self.known[entity_id].file, entity_id)

    def _entity(self, dotted: str, hops: int) -> str | None:
        """Map a dotted target to a repository entity, following re-exports."""
        if hops > _MAX_HOPS:
            return None
        if dotted in self.known:
            return self._callable(dotted)
        # Split into the longest known module prefix and the remainder.
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix, remainder = ".".join(parts[:i]), parts[i:]
            mod = self.modules.get(prefix)
            if mod is None:
                continue
            bound = mod.names.get(remainder[0])
            if bound is None or bound == dotted:
                return None
            return self._entity(_join(bound, ".".join(remainder[1:])), hops + 1)
        return None

    def _callable(self, entity_id: str) -> str:
        """Calling a class runs its `__init__` when the class defines one."""
        if self.known[entity_id].kind == "class" and f"{entity_id}.__init__" in self.known:
            return f"{entity_id}.__init__"
        return entity_id

    def _method(self, class_fqn: str, attr: str, hops: int) -> str | None:
        """`self.attr(...)`: the class's own method, then repository base classes."""
        if hops > _MAX_HOPS or "." in attr:
            return None  # `self.repo.save()` needs type inference; leave it
        own = f"{class_fqn}.{attr}"
        if own in self.known:
            return own
        mod = self.modules.get(self._module_of(class_fqn))
        if mod is None:
            return None
        for base in mod.bases.get(class_fqn, []):
            head, _, rest = base.partition(".")
            target = mod.names.get(head)
            if target is None:
                continue
            base_id = self._entity(_join(target, rest), 0)
            if base_id is None:
                continue
            if base_id.endswith(".__init__"):
                base_id = base_id.rsplit(".", 1)[0]
            found = self._method(base_id, attr, hops + 1)
            if found is not None:
                return found
        return None


def _unique_method(expr: str, methods_by_name: dict[str, list[str]]) -> str | None:
    """Unknown receiver: link only when exactly one repository method has that name."""
    if "." not in expr:
        return None
    attr = expr.rsplit(".", 1)[-1]
    if attr in _BUILTIN_METHOD_NAMES or (attr.startswith("__") and attr.endswith("__")):
        return None
    candidates = methods_by_name.get(attr, [])
    return candidates[0] if len(candidates) == 1 else None
