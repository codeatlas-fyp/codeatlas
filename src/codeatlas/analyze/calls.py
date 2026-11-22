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

Local scope: a name bound inside a function (a parameter, an assignment, a `for`/`with`/
`except`/walrus target, a nested `def` or `class`) is a local variable for the whole function,
so a call through it is not resolved to the module-level name it shadows. `global` names stay
module-level. An import inside a function binds only in that function and its nested
functions; it never leaks to the rest of the module.

Parameter defaults, annotations and decorators run when the `def` runs, so calls in them are
read in the enclosing scope and credited to the enclosing entity. A lambda's parameters and a
comprehension's loop variables are local to the lambda or comprehension.

Names bound more than once, at module level:

- one target only (a `def`, `class` or import, even if bound twice): confident, as above;
- one `def`/`class`/import target plus assignments, as in the optional-import idiom
  `try: from x import Hook` / `except ImportError: Hook = None`, or a `def` later rebound by
  `f = g` or through `global`: an edge to that target with `confident=False`;
- two different `def`/`class`/import targets (`try: from a import x` / `except: from b import
  x`): no edge, because the source text does not say which one runs;
- assignments only (`h = g`): an unknown value, so no edge for `h()`.

Inside a function, a name that is both imported and assigned, or imported from two places, is
treated as a local variable: no edge for `x()`. In every unresolved case `x.m()` may still take
the unconfident guess in step 6.

Calls made at module level have the module entity as `src`. Calls inside a
nested function are credited to the nearest enclosing entity, since nested
functions are not entities. Self-calls (recursion) are dropped, matching how
`build_import_graph` drops self-imports.

`resolve_call_sites` returns the same resolution per call site (file, line, column), before
pairs are merged into edges; the CALLS evaluation (`analyze/calls_eval.py`) scores it against
hand labels.

Pure: no file I/O, deterministic output order (sorted by src, dst).
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from codeatlas.analyze.code import _parser
from codeatlas.schema import CodeEdge, CodeEntity

if TYPE_CHECKING:
    import tree_sitter

EXTRACTOR_VERSION = "calls@0.3.0"
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
    ambiguous: set[str] = field(default_factory=set)  # bound to different things; never resolved
    uncertain: dict[str, str] = field(default_factory=dict)  # one target plus assignments
    bases: dict[str, list[str]] = field(default_factory=dict)  # class fqn -> base targets


@dataclass(frozen=True)
class _Scope:
    """Names bound inside the enclosing function(s) of a call."""

    names: frozenset[str] = frozenset()  # local variables: never resolved through module names
    imports: tuple[tuple[str, str], ...] = ()  # local name -> dotted target, from local imports

    def enter(self, names: set[str], imports: dict[str, str]) -> _Scope:
        """The scope of a function nested in this one: its own bindings hide the outer ones."""
        merged = {k: v for k, v in self.imports if k not in names}
        merged.update(imports)
        outer = {n for n in self.names if n not in imports}
        return _Scope(names=frozenset(outer | names), imports=tuple(sorted(merged.items())))


_MODULE_SCOPE = _Scope()


@dataclass(frozen=True)
class _Call:
    src: str
    expr: str  # dotted callee as written, e.g. "fees.calc" or "self.save"
    enclosing_class: str | None
    scope: _Scope = _MODULE_SCOPE
    file: str = ""
    line: int = 0  # 1-based line of the call expression
    column: int = 0  # 1-based byte column of the call expression


@dataclass(frozen=True)
class CallSite:
    """How one call expression was resolved, before call sites are merged into edges."""

    file: str
    line: int  # 1-based
    column: int  # 1-based, in bytes (tree-sitter columns)
    caller: str  # entity the call is credited to
    call_text: str  # dotted callee as written, e.g. "fees.calc"
    callee: str | None  # entity id, or None: no edge (external, unknown, or recursion)
    confident: bool  # meaningful only when callee is not None


def build_call_graph(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
) -> list[CodeEdge]:
    """Return CALLS edges between repository entities, sorted by (src, dst)."""
    edges = {
        (site.caller, site.callee, site.confident)
        for site in resolve_call_sites(entities=entities, file_sources=file_sources)
        if site.callee is not None
    }
    # A pair seen both confidently and as a guess keeps only the confident edge.
    confident_pairs = {(s, d) for s, d, c in edges if c}
    return [
        CodeEdge(src=s, dst=d, kind="CALLS", confident=c)
        for s, d, c in sorted(edges)
        if c or (s, d) not in confident_pairs
    ]


def resolve_call_sites(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
) -> list[CallSite]:
    """Every recorded call site with its resolution, sorted by (file, line, column).

    Calls with no dotted callee (`x[0]()`, `f()()`, `super().m()`) are not recorded. Recursion
    (callee == caller) gets `callee=None`, since `build_call_graph` drops it.
    """
    collected = _collect(entities, file_sources)
    if collected is None:
        return []  # no tree-sitter: no call graph, never a crash
    known, modules, module_of_file, calls = collected
    methods_by_name: dict[str, list[str]] = {}
    for e in entities:
        if e.kind == "method":
            methods_by_name.setdefault(e.entity_id.rsplit(".", 1)[-1], []).append(e.entity_id)

    resolver = _Resolver(known=known, modules=modules, module_of_file=module_of_file)
    sites: list[CallSite] = []
    for call in calls:
        dst, bound, confident = resolver.resolve(call)
        if dst is None and not bound:
            dst = _unique_method(call.expr, methods_by_name)
            confident = False
        if dst == call.src:
            dst = None
        sites.append(
            CallSite(
                file=call.file,
                line=call.line,
                column=call.column,
                caller=call.src,
                call_text=call.expr,
                callee=dst,
                confident=confident if dst is not None else False,
            )
        )
    return sorted(sites, key=lambda s: (s.file, s.line, s.column, s.caller, s.call_text))


STRATA = ("local", "cross_file", "ambiguous_dynamic", "external")


def call_site_strata(
    *,
    entities: list[CodeEntity],
    file_sources: dict[str, bytes],
) -> dict[tuple[str, int, int], str]:
    """A sampling stratum per recorded call site, from name bindings only, never from resolution.

    - `local`: `self.m()`/`cls.m()` calls, and names bound to a def, class or import in the same
      module;
    - `cross_file`: names bound to another repository module;
    - `external`: names bound to a module outside the repository, and builtins;
    - `ambiguous_dynamic`: local variables and parameters, names bound more than once, attribute
      chains on `self`/`cls` (`self.repo.save()`), `super()` calls, and unbound receivers.

    Call sites the resolver never records (`x[0]()`, `super().m()`) are not in the result; the
    evaluation puts them in `ambiguous_dynamic`. Keyed by (file, line, column).
    """
    collected = _collect(entities, file_sources)
    if collected is None:
        return {}
    _known, modules, module_of_file, calls = collected
    repo_modules = set(modules) | {e.entity_id for e in entities if e.kind == "module"}
    builtin_names = set(dir(builtins))
    out: dict[tuple[str, int, int], str] = {}
    for call in calls:
        mod = modules[module_of_file[call.file]]
        out[(call.file, call.line, call.column)] = _stratum(call, mod, repo_modules, builtin_names)
    return out


def _stratum(call: _Call, mod: _Module, repo_modules: set[str], builtin_names: set[str]) -> str:
    head, _, rest = call.expr.partition(".")
    if head in ("self", "cls") and call.enclosing_class and rest:
        return "local" if "." not in rest else "ambiguous_dynamic"  # self.repo.save()
    if head == "super":
        return "ambiguous_dynamic"
    local_import = dict(call.scope.imports).get(head)
    if local_import is not None:
        return _target_stratum(_join(local_import, rest), mod.name, repo_modules)
    if head in call.scope.names or head in mod.uncertain or head in mod.ambiguous:
        return "ambiguous_dynamic"
    target = mod.names.get(head)
    if target is not None:
        return _target_stratum(_join(target, rest), mod.name, repo_modules)
    return "external" if head in builtin_names else "ambiguous_dynamic"


def _target_stratum(target: str, module: str, repo_modules: set[str]) -> str:
    parts = target.split(".")
    for end in range(len(parts), 0, -1):
        prefix = ".".join(parts[:end])
        if prefix in repo_modules:
            return "local" if prefix == module else "cross_file"
    return "external"


def _collect(
    entities: list[CodeEntity], file_sources: dict[str, bytes]
) -> tuple[dict[str, CodeEntity], dict[str, _Module], dict[str, str], list[_Call]] | None:
    """Parse every module file once: bindings per module and every recorded call."""
    try:
        parser = _parser()
    except ImportError:
        return None
    known = {e.entity_id: e for e in entities}
    module_of_file = {e.file: e.entity_id for e in entities if e.kind == "module"}
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
        found: list[_Call] = []
        _collect_calls(tree.root_node, source, mod, known, module_name, None, _MODULE_SCOPE, found)
        calls += [replace(call, file=path) for call in found]
    return known, modules, module_of_file, calls


# ---- bindings ------------------------------------------------------------


def _text(node: tree_sitter.Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


_ASSIGNED = "<assigned>"  # a binding whose value is not known statically


def _collect_bindings(root: tree_sitter.Node, source: bytes, mod: _Module) -> None:
    """Fill `mod.names` from module-level bindings; a name bound to different things is ambiguous.

    Module-level bindings are top-level `def`/`class` names, imports (also under `if`/`try`),
    assignment-like targets outside functions and classes, and names a function rebinds through
    `global`. Class bodies bind class attributes, not module names, so they are skipped.
    """
    bindings: dict[str, set[str]] = {}
    _collect_module_bindings(root, source, mod, bindings)
    _collect_global_rebinds(root, source, mod, bindings)
    for name, targets in bindings.items():
        concrete = targets - {_ASSIGNED}
        if len(concrete) == 1 and _ASSIGNED not in targets:
            mod.names[name] = next(iter(concrete))
        elif len(concrete) == 1:
            mod.uncertain[name] = next(iter(concrete))
        else:
            mod.ambiguous.add(name)


def _collect_module_bindings(
    node: tree_sitter.Node, source: bytes, mod: _Module, bindings: dict[str, set[str]]
) -> None:
    for child in node.children:
        kind = child.type
        if kind in ("function_definition", "class_definition"):
            name = child.child_by_field_name("name")
            if name is not None:
                bindings.setdefault(_text(name, source), set()).add(
                    f"{mod.name}.{_text(name, source)}"
                )
            continue  # their bodies are not module scope
        if kind in _OWN_SCOPE or kind in ("string", "comment"):
            continue
        if kind in ("import_statement", "import_from_statement"):
            for local, target in _import_bindings(child, source, mod).items():
                bindings.setdefault(local, set()).add(target)
            continue
        for bound in _bound_names(child, source):
            bindings.setdefault(bound, set()).add(_ASSIGNED)
        _collect_module_bindings(child, source, mod, bindings)


def _collect_global_rebinds(
    node: tree_sitter.Node, source: bytes, mod: _Module, bindings: dict[str, set[str]]
) -> None:
    """`def f(): global x; x = ...` rebinds module-level `x` at run time."""
    for child in node.children:
        if child.type == "function_definition":
            names: set[str] = set()
            imports: dict[str, set[str]] = {}
            declared_global: set[str] = set()
            body = child.child_by_field_name("body")
            if body is not None:
                _scan_locals(body, source, mod, names, imports, declared_global)
            for name in declared_global & (names | set(imports)):
                bindings.setdefault(name, set()).add(_ASSIGNED)
        if child.type not in ("string", "comment"):
            _collect_global_rebinds(child, source, mod, bindings)


def _import_bindings(stmt: tree_sitter.Node, source: bytes, mod: _Module) -> dict[str, str]:
    """Local name -> dotted target for one `import` or `from ... import` statement."""
    out: dict[str, str] = {}
    if stmt.type == "import_statement":
        for item in stmt.children_by_field_name("name"):
            if item.type == "aliased_import":
                target = item.child_by_field_name("name")
                alias = item.child_by_field_name("alias")
                if target is not None and alias is not None:
                    out[_text(alias, source)] = _text(target, source)
            else:
                head = _text(item, source).split(".", 1)[0]
                out.setdefault(head, head)  # `import a.b` binds `a`
        return out
    module_node = stmt.child_by_field_name("module_name")
    if module_node is None:
        return out
    base = _resolve_from(_text(module_node, source), mod)
    for item in stmt.children_by_field_name("name"):
        if item.type == "aliased_import":
            target = item.child_by_field_name("name")
            alias = item.child_by_field_name("alias")
            if target is not None and alias is not None:
                out[_text(alias, source)] = _join(base, _text(target, source))
        else:
            name = _text(item, source)
            out[name.split(".")[-1]] = _join(base, name)
    return out


# ---- local scope -----------------------------------------------------------

# Nodes with a scope of their own: names bound inside them are not locals of the function.
_OWN_SCOPE = frozenset(
    {
        "lambda",
        "list_comprehension",
        "set_comprehension",
        "dictionary_comprehension",
        "generator_expression",
    }
)


def _function_scope(
    fn: tree_sitter.Node, source: bytes, mod: _Module
) -> tuple[set[str], dict[str, str]]:
    """(local variable names, local imports) of one function, as Python scoping defines them."""
    names: set[str] = set()
    imports: dict[str, set[str]] = {}
    declared_global: set[str] = set()
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            names |= _param_names(p, source)
    body = fn.child_by_field_name("body")
    if body is not None:
        _scan_locals(body, source, mod, names, imports, declared_global)
    for name in declared_global:
        names.discard(name)
        imports.pop(name, None)
    resolved: dict[str, str] = {}
    for name, targets in imports.items():
        if len(targets) == 1 and name not in names:
            resolved[name] = next(iter(targets))
        else:
            names.add(name)  # imported from two places, or imported and assigned: ambiguous
    return names, resolved


def _param_names(node: tree_sitter.Node, source: bytes) -> set[str]:
    if node.type == "identifier":
        return {_text(node, source)}
    if node.type in ("default_parameter", "typed_default_parameter"):
        name = node.child_by_field_name("name")
        return _param_names(name, source) if name is not None else set()
    if node.type in ("typed_parameter", "list_splat_pattern", "dictionary_splat_pattern"):
        out: set[str] = set()
        for child in node.named_children:
            if child.type != "type":
                out |= _param_names(child, source)
        return out
    if node.type in ("tuple_pattern", "list_pattern"):
        return _target_names(node, source)
    return set()


def _target_names(node: tree_sitter.Node, source: bytes) -> set[str]:
    """Names an assignment target binds; `obj.x = ...` and `d[k] = ...` bind none."""
    if node.type == "identifier":
        return {_text(node, source)}
    if node.type in ("attribute", "subscript"):
        return set()
    out: set[str] = set()
    for child in node.named_children:
        out |= _target_names(child, source)
    return out


def _bound_names(node: tree_sitter.Node, source: bytes) -> set[str]:
    """Names one assignment-like node binds: assignment, `for`, walrus or `as` target."""
    kind = node.type
    if kind in ("assignment", "augmented_assignment", "for_statement"):
        left = node.child_by_field_name("left")
        return _target_names(left, source) if left is not None else set()
    if kind == "named_expression":
        target = node.child_by_field_name("name")
        return _target_names(target, source) if target is not None else set()
    if kind == "as_pattern":
        alias = node.child_by_field_name("alias")
        return _target_names(alias, source) if alias is not None else set()
    return set()


def _scan_locals(
    node: tree_sitter.Node,
    source: bytes,
    mod: _Module,
    names: set[str],
    imports: dict[str, set[str]],
    declared_global: set[str],
) -> None:
    for child in node.children:
        kind = child.type
        if kind in ("function_definition", "class_definition"):
            name = child.child_by_field_name("name")
            if name is not None:
                names.add(_text(name, source))
            continue  # its body is its own scope
        if kind in _OWN_SCOPE:
            continue
        if kind in ("import_statement", "import_from_statement"):
            for local, target in _import_bindings(child, source, mod).items():
                imports.setdefault(local, set()).add(target)
            continue
        if kind == "global_statement":
            declared_global |= {_text(c, source) for c in child.named_children}
            continue
        names |= _bound_names(child, source)
        _scan_locals(child, source, mod, names, imports, declared_global)


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
    scope: _Scope,
    out: list[_Call],
) -> None:
    """Depth-first: track the enclosing entity and scope, and record each call under it."""
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
                _collect_calls(child, source, mod, known, fqn, fqn, scope, out)
            else:
                _collect_calls(child, source, mod, known, current, enclosing_class, scope, out)
            continue
        if child.type == "function_definition":
            name = child.child_by_field_name("name")
            fqn = f"{current}.{_text(name, source)}" if name else None
            inner = scope.enter(*_function_scope(child, source, mod))
            owner = (
                fqn
                if fqn and fqn in known and known[current].kind in ("module", "class")
                else current
            )
            for part in child.children:
                if part.type == "block":  # the body runs in the function's own scope
                    _collect_calls(part, source, mod, known, owner, enclosing_class, inner, out)
                else:  # defaults and annotations run when the `def` runs
                    _collect_calls(part, source, mod, known, current, enclosing_class, scope, out)
            continue
        if child.type == "lambda":
            params = child.child_by_field_name("parameters")
            bound: set[str] = set()
            if params is not None:
                for p in params.named_children:
                    bound |= _param_names(p, source)
            body = child.child_by_field_name("body")
            if body is not None:
                inner = scope.enter(bound, {})
                _collect_calls(body, source, mod, known, current, enclosing_class, inner, out)
                if body.type == "call":
                    _record_call(body, source, current, enclosing_class, inner, out)
            continue
        if child.type in _COMPREHENSIONS:
            loop_vars: set[str] = set()
            for clause in child.named_children:
                if clause.type == "for_in_clause":
                    left = clause.child_by_field_name("left")
                    if left is not None:
                        loop_vars |= _target_names(left, source)
            inner = scope.enter(loop_vars, {})
            _collect_calls(child, source, mod, known, current, enclosing_class, inner, out)
            continue
        if child.type == "call":
            _record_call(child, source, current, enclosing_class, scope, out)
        _collect_calls(child, source, mod, known, current, enclosing_class, scope, out)


_COMPREHENSIONS = frozenset(
    {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}
)


def _record_call(
    node: tree_sitter.Node,
    source: bytes,
    current: str,
    enclosing_class: str | None,
    scope: _Scope,
    out: list[_Call],
) -> None:
    fn = node.child_by_field_name("function")
    expr = _dotted(fn, source) if fn is not None else None
    if expr is not None:
        row, column = node.start_point
        out.append(
            _Call(
                src=current,
                expr=expr,
                enclosing_class=enclosing_class,
                scope=scope,
                line=row + 1,
                column=column + 1,
            )
        )


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

    def resolve(self, call: _Call) -> tuple[str | None, bool, bool]:
        """Return (entity or None, whether the head name is bound, whether the edge is confident).

        A bound head that resolves to nothing is an external call (`json.dumps`) and must not
        fall back to the unique-method guess.
        """
        head, _, rest = call.expr.partition(".")
        mod = self.modules.get(self._module_of(call.src))
        if mod is None:
            return None, False, True
        if head in ("self", "cls") and call.enclosing_class and rest:
            return self._method(call.enclosing_class, rest, 0), "." not in rest, True
        local_import = dict(call.scope.imports).get(head)
        if local_import is not None:
            return self._entity(_join(local_import, rest), 0), True, True
        if head in call.scope.names:
            # A local variable: its value is unknown here. `x()` gets no edge; `x.m()` may
            # still take the unique-method guess (confident=False), as for any unknown receiver.
            return None, False, True
        if head in mod.uncertain:
            # One real target plus fallback assignments (`except ImportError: X = None`).
            # An external target (`json`) stays bound, so it never takes the unique-method guess.
            return self._entity(_join(mod.uncertain[head], rest), 0), True, False
        if head in mod.ambiguous:
            return None, False, True  # bound to different things: an unknown receiver
        target = mod.names.get(head)
        if target is None:
            return None, False, True
        return self._entity(_join(target, rest), 0), True, True

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
