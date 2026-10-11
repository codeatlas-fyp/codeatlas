"""Print the `uv sync` flags that leave sentence-transformers and its stack out of the image.

The web server only reads stored bundles: semantic scores are frozen inside them, so the
server never loads an embedding model. sentence-transformers pulls torch and the CUDA
libraries (several GB). This script reads uv.lock and prints `--no-install-package X` for
every package that is needed *only* through sentence-transformers, so the list stays
correct when teammates add or upgrade dependencies. The lockfile itself is never changed.

Usage (inside the Dockerfile):  uv sync --locked --no-dev $(python scripts/docker_excludes.py)
"""

import sys
import tomllib
from pathlib import Path

PROJECT = "codeatlas"
HEAVY = "sentence-transformers"


def dependency_graph(lock: dict[str, object]) -> dict[str, set[str]]:
    """Package name -> names it depends on, including the packages behind requested extras."""
    packages = {p["name"]: p for p in lock["package"]}  # type: ignore[attr-defined]
    graph: dict[str, set[str]] = {}
    for name, package in packages.items():
        edges: set[str] = set()
        for dep in package.get("dependencies", []):
            edges.add(dep["name"])
            optional = packages.get(dep["name"], {}).get("optional-dependencies", {})
            for extra in dep.get("extra", []):
                edges.update(d["name"] for d in optional.get(extra, []))
        graph[name] = edges
    return graph


def reachable(graph: dict[str, set[str]], start: str, skip: str | None = None) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        for dep in graph.get(stack.pop(), set()):
            if dep != skip and dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def excluded_packages(lock_path: Path) -> list[str]:
    graph = dependency_graph(tomllib.loads(lock_path.read_text(encoding="utf-8")))
    return sorted(reachable(graph, PROJECT) - reachable(graph, PROJECT, skip=HEAVY))


def main(argv: list[str]) -> int:
    lock_path = Path(argv[1]) if len(argv) > 1 else Path("uv.lock")
    print(" ".join(f"--no-install-package {name}" for name in excluded_packages(lock_path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
