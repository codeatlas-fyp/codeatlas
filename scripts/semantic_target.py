"""Which code a semantic script reads: CodeAtlas's own `src`, or another repo at a pinned SHA.

    --repo D:/airflow@4f1c2ab    that repository's .py files at that commit
    --repo D:/data/airflow            the same at its HEAD (resolved to a full SHA)
    (no --repo)                       CodeAtlas's own src/ working tree, pinned as "working-tree"
    --root python/                    only files under that folder (a case's analysis root)

Labels written against Airflow or Arrow only make sense on a corpus built from that repository
at the commit the labels were made for, so the SHA is resolved and recorded with every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codeatlas.analyze.code import extract_entities
from codeatlas.schema import CodeEntity

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Target:
    label: str  # "codeatlas" or the repo folder name
    pinned_sha: str
    sources: dict[str, bytes]
    entities: list[CodeEntity]


def split_spec(spec: str) -> tuple[Path, str]:
    """ "PATH@REF" -> (PATH, REF); a bare PATH means HEAD. Windows drive colons are fine."""
    path, sep, ref = spec.rpartition("@")
    if not sep or not path or "/" in ref or "\\" in ref:
        return Path(spec), "HEAD"
    return Path(path), ref or "HEAD"


def under_root(path: str, root: str) -> bool:
    """True when `path` is inside the analysis root ("" or "/" means the whole repository)."""
    root = root.strip("/")
    return not root or path.startswith(root + "/")


def load_target(spec: str | None, root: str = "") -> Target:
    """The code a run reads. `root` keeps only files under that folder, as prepare_case.py's
    --root does for entities.csv, so a run and its labelling packet see the same code."""
    if spec is None:
        src = ROOT / "src"
        sources = {p.relative_to(src).as_posix(): p.read_bytes() for p in sorted(src.rglob("*.py"))}
        label, sha = "codeatlas", "working-tree"
    else:
        from codeatlas.collect.git.repo import LocalRepo

        path, ref = split_spec(spec)
        repo = LocalRepo(path)
        sha = repo.resolve(ref)
        sources = repo.python_sources(sha)
        label = path.resolve().name
    sources = {name: text for name, text in sources.items() if under_root(name, root)}
    entities = [
        e
        for path, text in sorted(sources.items())
        for e in extract_entities(file_path=path, source=text)
    ]
    return Target(label=label, pinned_sha=sha, sources=sources, entities=entities)
