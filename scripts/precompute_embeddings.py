"""Embed a repository's code ahead of the semantic runs, without any criterion (dataset task 9).

    uv run python scripts/precompute_embeddings.py --repo D:/eval/repos/airflow@<sha>
    uv run python scripts/precompute_embeddings.py --repos-dir D:/FYP/codeatlas-eval/eval/repos
        --case D:/FYP/codeatlas-eval/eval/cases/AF-01 --case D:/FYP/codeatlas-eval/eval/cases/AR-01

Builds each target's corpus exactly as a run does (same chunks, same text, same analysis root)
and embeds every chunk with MiniLM and CodeT5+ into the on-disk cache (.codeatlas-cache/).
A later export_semantic_run.py or run_semantic_cases.py on the same code then reads the
vectors back instead of computing them.

Only code is embedded. No criterion, issue text or label is read, so running this before
the labels are frozen tells nobody anything about the labels or about CodeAtlas's answers.
Cached vectors are keyed by model id and exact text, so a cache hit can never be stale.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from frozen_case import read_case
from semantic_target import ROOT, load_target

from codeatlas.analyze.codet5p import EmbedderUnavailable
from codeatlas.analyze.semantic_corpus import build_corpus
from codeatlas.analyze.semantic_run import Loader, default_loaders, load_once

CACHE = ROOT / ".codeatlas-cache"
EXIT_MODEL_UNAVAILABLE = 2


def targets(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    """(label, PATH@SHA, analysis root) for every target asked for."""
    out = [(args.repo, args.repo, args.root)] if args.repo else []
    for folder in args.case:
        case = read_case(folder)
        out.append((case.case_id, case.target_spec(args.repos_dir), case.analysis_root))
    return out


def precompute(spec: str, root: str, loaders: dict[str, Loader], batch: int) -> list[str]:
    """Embed one target's chunks with every loader; one printable line per model."""
    target = load_target(spec, root)
    corpus = build_corpus(
        entities=target.entities, file_sources=target.sources, pinned_sha=target.pinned_sha
    )
    texts = [chunk.text for chunk in corpus.chunks()]
    lines = [
        f"{target.label} @ {target.pinned_sha}: {len(corpus.docs)} entities, {len(texts)} chunks"
    ]
    for name, load in loaders.items():
        started = time.perf_counter()
        embedder = load()
        cache = getattr(embedder, "cache", None)
        before = (cache.hits, cache.misses) if cache is not None else (0, 0)
        for start in range(0, len(texts), batch):
            embedder.embed(texts[start : start + batch])
        seconds = time.perf_counter() - started
        if cache is None:
            lines.append(f"   {name:8} embedded {len(texts)} chunks in {seconds:.0f}s (no cache!)")
        else:
            hits, misses = cache.hits - before[0], cache.misses - before[1]
            lines.append(
                f"   {name:8} {hits} already cached, {misses} new, {seconds:.0f}s "
                f"({embedder.model_id})"
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None, help="PATH@SHA of one target")
    parser.add_argument("--root", default="", help="analysis root for --repo, e.g. python/")
    parser.add_argument("--case", type=Path, action="append", default=[], help="repeatable")
    parser.add_argument("--repos-dir", type=Path, default=None, help="clones for --case")
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--batch", type=int, default=256, help="chunks per embed call")
    args = parser.parse_args(argv)
    if not args.repo and not args.case:
        parser.error("give --repo or at least one --case")
    if args.case and args.repos_dir is None:
        parser.error("--case needs --repos-dir (the folder with the repository clones)")

    loaders = load_once(default_loaders(args.cache))  # each model loads once for all targets
    for label, spec, root in targets(args):
        print(f"[{label}]")
        try:
            for line in precompute(spec, root, loaders, args.batch):
                print(line)
        except EmbedderUnavailable as exc:
            print(f"   a model could not load: {exc}")
            return EXIT_MODEL_UNAVAILABLE
    print(f"cache: {args.cache}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
