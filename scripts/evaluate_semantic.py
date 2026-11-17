"""Score semantic runs against labels on the full rankings, not a fixed top 5.

    uv run python scripts/evaluate_semantic.py --run runs/<run_id> --labels labels.csv
    uv run python scripts/evaluate_semantic.py --run runs/AF-01 --run runs/AR-03 --labels all.csv
        --out eval-results

Reads each run's rankings.csv (every entity each ranker ranked), or its predictions.csv when an
older run has no rankings.csv (then ranks stop at the exported top rows and the output says so).
Writes evaluation.md, evaluation.json, recall_curve.csv and recall_curve.svg to --out
(default: the run folder when there is one run) and prints the tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codeatlas.analyze.semantic_eval import (
    as_dict,
    evaluate,
    markdown,
    read_rows,
    recall_curve_csv,
    recall_curve_svg,
)
from codeatlas.analyze.semantic_export import CSV_NAME, RANKINGS_NAME
from codeatlas.analyze.semantic_gate import read_dev_labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, action="append", required=True, help="repeatable")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows: list[dict[str, str]] = []
    sources: set[str] = set()
    for run in args.run:
        if (run / RANKINGS_NAME).is_file():
            rows += read_rows(run / RANKINGS_NAME)
            sources.add(f"{RANKINGS_NAME} (full)")
        elif (run / CSV_NAME).is_file():
            rows += read_rows(run / CSV_NAME)
            sources.add(f"{CSV_NAME} (top rows only)")
        else:
            print(f"no {RANKINGS_NAME} or {CSV_NAME} in {run}")
            return 1
    out = args.out or (args.run[0] if len(args.run) == 1 else None)
    if out is None:
        print("several runs: pass --out for the results folder")
        return 1

    result = evaluate(rows, read_dev_labels(args.labels), source=" + ".join(sorted(sources)))
    out.mkdir(parents=True, exist_ok=True)
    text = markdown(result)
    (out / "evaluation.md").write_text(text, encoding="utf-8")
    data = as_dict(result) | {"labels": args.labels.name}
    (out / "evaluation.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "recall_curve.csv").write_text(recall_curve_csv(result), encoding="utf-8")
    (out / "recall_curve.svg").write_text(recall_curve_svg(result), encoding="utf-8")
    print(text)
    for name in ("evaluation.md", "evaluation.json", "recall_curve.csv", "recall_curve.svg"):
        print(f"wrote {out / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
