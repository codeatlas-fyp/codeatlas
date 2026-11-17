"""Score a semantic-run-v1 export against labels: recall@k, hit@k and MRR per ranker.

    uv run python scripts/evaluate_semantic.py --run runs/<run_id> --labels labels.csv

Reads <run>/predictions.csv, prints the tables and writes <run>/evaluation.md and
<run>/evaluation.json next to it. Labels made from the same code the run read give the
honest numbers; the CodeAtlas self dev set is the provisional check until the dataset
labels exist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codeatlas.analyze.semantic_eval import as_dict, evaluate, markdown, read_predictions
from codeatlas.analyze.semantic_export import CSV_NAME
from codeatlas.analyze.semantic_gate import read_dev_labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True, help="a runs/<run_id> folder")
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args(argv)

    predictions = args.run / CSV_NAME
    if not predictions.is_file():
        print(f"no {CSV_NAME} in {args.run}")
        return 1
    result = evaluate(read_predictions(predictions), read_dev_labels(args.labels))
    text = markdown(result)
    (args.run / "evaluation.md").write_text(text, encoding="utf-8")
    data = as_dict(result) | {"labels": args.labels.name}
    (args.run / "evaluation.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(text)
    print(f"wrote {args.run / 'evaluation.md'}\nwrote {args.run / 'evaluation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
