"""Mutation score for chosen modules, using simple AST mutations.

mutmut, which the task asked for, refuses to run on native Windows ("To run mutmut on Windows,
please use the WSL", mutmut issue #397), and this machine has no WSL distribution or Docker. This
script applies the usual mutation operators one at a time, runs the given tests against each
mutant, and counts killed and surviving mutants. It works on a copy of the repository, so an
interrupted run never leaves a mutated file behind.

Operators: comparison flips (< <=, > >=, == !=, in / not in, is / is not), `and` <-> `or`,
removing `not`, and True <-> False.

Usage: uv run python scripts/mutation_score.py --out result.json \
           --target src/codeatlas/analyze/lifecycle.py --tests tests/unit/test_lifecycle.py ...
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_COMPARE = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}


def _sites(tree: ast.AST) -> list[tuple[int, str]]:
    """(node id, description) of every place a mutation can be applied, in a stable order."""
    sites = []
    for index, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare) and type(node.ops[0]) in _COMPARE:
            sites.append((index, f"line {node.lineno}: {type(node.ops[0]).__name__} flipped"))
        elif isinstance(node, ast.BoolOp):
            sites.append((index, f"line {node.lineno}: {type(node.op).__name__} swapped"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((index, f"line {node.lineno}: `not` removed"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            sites.append((index, f"line {node.lineno}: {node.value} negated"))
    return sites


class _Mutate(ast.NodeTransformer):
    def __init__(self, target: int) -> None:
        self._target = target
        self._index = -1
        self._ids: dict[int, int] = {}

    def run(self, tree: ast.AST) -> ast.AST:
        self._ids = {id(node): i for i, node in enumerate(ast.walk(tree))}
        return self.visit(tree)

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if self._ids.get(id(node)) == self._target:
            return self._apply(node)
        return super().generic_visit(node)

    def _apply(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.Compare):
            node.ops[0] = _COMPARE[type(node.ops[0])]()
        elif isinstance(node, ast.BoolOp):
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        elif isinstance(node, ast.UnaryOp):
            return node.operand
        elif isinstance(node, ast.Constant):
            node.value = not node.value
        return node


def _passes(workdir: Path, tests: list[str]) -> bool:
    # The copy's src/ must win over the editable install, or the mutants would never be imported.
    env = {**os.environ, "PYTHONPATH": str(workdir / "src") + os.pathsep + str(workdir)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
        cwd=workdir,
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )
    return result.returncode == 0


def _check_isolation(workdir: Path) -> None:
    """Prove the tests import the copy: a broken copy must make them fail."""
    probe = workdir / "src" / "codeatlas" / "evaluate" / "verdict.py"
    original = probe.read_text(encoding="utf-8")
    probe.write_text(original + "\nraise RuntimeError('isolation probe')\n", encoding="utf-8")
    try:
        if _passes(workdir, ["tests/unit/test_verdict.py"]):
            raise SystemExit("tests do not import the copied src/: mutation results would be wrong")
    finally:
        probe.write_text(original, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True)
    parser.add_argument("--tests", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "repo"
        shutil.copytree(ROOT, workdir, ignore=shutil.ignore_patterns(".venv", ".git", "*cache*"))
        _check_isolation(workdir)
        if not _passes(workdir, args.tests):
            raise SystemExit("tests fail before any mutation")
        report = []
        for target in args.target:
            path = workdir / target
            original = path.read_text(encoding="utf-8")
            tree = ast.parse(original)
            for site, description in _sites(tree):
                mutant = _Mutate(site).run(ast.parse(original))
                path.write_text(ast.unparse(mutant), encoding="utf-8")
                killed = not _passes(workdir, args.tests)
                path.write_text(original, encoding="utf-8")
                report.append({"file": target, "mutation": description, "killed": killed})
                print(f"{'killed  ' if killed else 'SURVIVED'} {target} {description}", flush=True)

    killed = sum(1 for r in report if r["killed"])
    summary = {
        "tool": "scripts/mutation_score.py (AST mutations; mutmut does not run on native Windows)",
        "targets": args.target,
        "mutants": len(report),
        "killed": killed,
        "survived": len(report) - killed,
        "score": round(killed / len(report), 4) if report else None,
        "results": report,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"score {killed}/{len(report)}")


if __name__ == "__main__":
    main()
