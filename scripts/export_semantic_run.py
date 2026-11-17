"""Run CA-02 semantic retrieval on a repository and write a semantic-run-v1 export.

    uv run python scripts/export_semantic_run.py --criteria calibration/dev-codeatlas-self.csv
    uv run python scripts/export_semantic_run.py --criteria labels.csv --repo D:/airflow@4f1c2ab

Reads criteria from a CSV with columns criterion_id, criterion_text (an entity_id column
is allowed and ignored, so a labels file works as is). Builds the body-inclusive corpus from
the target code (see semantic_target.py) and runs BM25, MiniLM and CodeT5+ on it. Applies
the calibrated gate and writes runs/<run_id>/semantic-run-v1.json and
runs/<run_id>/predictions.csv. The CSV is the input for Saleha's evaluation harness.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from semantic_target import ROOT, load_target

from codeatlas.analyze.semantic_export import write_export
from codeatlas.analyze.semantic_gate import load_calibration, read_dev_labels
from codeatlas.analyze.semantic_run import default_loaders, run_semantic

CACHE = ROOT / ".codeatlas-cache"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--repo", default=None, help="PATH or PATH@SHA; default CodeAtlas src")
    parser.add_argument("--out", type=Path, default=ROOT / "runs")
    parser.add_argument("--run-id", default=None, help="default <repo>-<sha8>-<UTC time>")
    parser.add_argument("--top-k", type=int, default=5, help="fused candidates per criterion")
    parser.add_argument("--top-n", type=int, default=10, help="rows per ranker per criterion")
    parser.add_argument("--calibration", type=Path, default=None)
    parser.add_argument("--bm25-only", action="store_true")
    args = parser.parse_args(argv)

    target = load_target(args.repo)
    criteria = [d.as_criterion() for d in read_dev_labels(args.criteria)]
    calibration = load_calibration(args.calibration) if args.calibration else load_calibration()
    result = run_semantic(
        criteria=criteria,
        entities=target.entities,
        file_sources=target.sources,
        pinned_sha=target.pinned_sha,
        top_k=args.top_k,
        bm25_only=args.bm25_only,
        calibration=calibration,
        loaders=default_loaders(CACHE),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"{target.label}-{target.pinned_sha[:8]}-{stamp}"
    json_path, csv_path = write_export(
        args.out / run_id,
        result,
        criteria,
        run_id=run_id,
        repo=target.label,
        top_n=args.top_n,
    )

    print(f"repo {target.label} @ {target.pinned_sha}")
    print(f"corpus {len(result.corpus.docs)} entities, {len(result.corpus.chunks())} chunks")
    for ranker in result.summary.rankers:
        print(f"   {ranker.name:8} {ranker.state:11} {ranker.model_id}")
    counts: dict[str, int] = {}
    for gate in result.gates:
        counts[gate.gate] = counts.get(gate.gate, 0) + 1
    print(f"gate ({result.summary.calibration_id}): {dict(sorted(counts.items()))}")
    print(f"wrote {json_path}\nwrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
