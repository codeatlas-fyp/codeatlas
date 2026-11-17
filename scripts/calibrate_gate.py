"""Fit the accept/review/abstain gate on labelled dev criteria (CA-02).

    uv run python scripts/calibrate_gate.py --labels calibration/dev-codeatlas-self.csv

Builds the corpus from this repository's `src`, runs BM25, MiniLM and CodeT5+, fits the
thresholds (see `semantic_gate.calibrate`), writes them to
`src/codeatlas/analyze/gate_calibration.json`, and prints what the gate now decides for
every dev criterion. Labels from CodeAtlas's own code give a PROVISIONAL calibration; the
real one is fitted the same way on the dataset's dev split.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codeatlas.analyze.code import extract_entities
from codeatlas.analyze.codet5p import load_codet5p
from codeatlas.analyze.minilm import load_minilm
from codeatlas.analyze.rankers import rank_bm25, rank_dense
from codeatlas.analyze.semantic_corpus import build_corpus
from codeatlas.analyze.semantic_gate import (
    DEFAULT_CALIBRATION_FILE,
    calibrate,
    decide,
    read_dev_labels,
)
from codeatlas.schema import Criterion

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".codeatlas-cache" / "embeddings"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_CALIBRATION_FILE)
    parser.add_argument("--id", default=None, help="calibration id, default gate@<labels stem>")
    parser.add_argument("--final", action="store_true", help="not provisional (real dev split)")
    args = parser.parse_args()

    dev = read_dev_labels(args.labels)
    sources = {
        p.relative_to(ROOT / "src").as_posix(): p.read_bytes()
        for p in sorted((ROOT / "src").rglob("*.py"))
    }
    entities = [e for p, s in sources.items() for e in extract_entities(file_path=p, source=s)]
    corpus = build_corpus(entities=entities, file_sources=sources, pinned_sha="working-tree")
    criteria = [
        Criterion(
            criterion_id=d.criterion_id,
            key=d.criterion_id.split(":")[0],
            version_no=1,
            text=d.text,
            derived_from_description=False,
        )
        for d in dev
    ]
    outputs = [
        rank_bm25(corpus, criteria),
        rank_dense("minilm", corpus, criteria, lambda: load_minilm(embedding_cache_dir=CACHE)),
        rank_dense("codet5p", corpus, criteria, lambda: load_codet5p(embedding_cache_dir=CACHE)),
    ]
    for out in outputs:
        print(f"{out.name:8} {out.state:11} {out.model_id}")

    calibration = calibrate(
        dev,
        outputs,
        calibration_id=args.id or f"gate@{args.labels.stem}",
        dev_set=args.labels,
        provisional=not args.final,
    )
    if not calibration.thresholds:
        print("\nno dense ranker ran, so there is nothing to calibrate; file not written")
        return 1
    args.out.write_text(calibration.to_json(), encoding="utf-8")
    print(f"\nwrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    for name, limits in calibration.thresholds.items():
        print(f"   {name:8} accept >= {limits.accept}   review >= {limits.review}")
        print(f"            {calibration.report[name]}")

    print("\ngate decisions on the dev criteria:")
    expected = {d.criterion_id: d.relevant for d in dev}
    for gate in decide([d.criterion_id for d in dev], outputs, calibration):
        truth = "match" if expected[gate.criterion_id] else "NO MATCH"
        print(f"   {gate.criterion_id}  {gate.gate:8} ({truth:8})  {gate.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
