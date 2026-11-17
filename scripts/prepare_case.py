"""Write the neutral files a labeller works from for one case (CA-02, build plan C8).

    uv run python scripts/prepare_case.py --repo D:/eval/repos/airflow@<head_sha>
        --key AIRFLOW-1234 --issue-text issue.txt --out D:/codeatlas-eval/eval/cases/AF-01

Writes into --out:

- entities.csv: every function, method and class at the case's head SHA, with the CodeAtlas
  id, the labeller's form (`path.py::Qualified.name`), kind, file, lines and whether it sits
  in a test file. This is a list of the whole candidate set, not a ranking: no model or
  score is involved, so labelling from it stays blind to CodeAtlas output.
- criteria.csv: the issue text split into acceptance criteria exactly as CodeAtlas splits
  it, each with its criterion_id (`KEY:vN:acM`), so labels and runs join on the same id.

--issue-text is a UTF-8 file with the issue's description and acceptance criteria as shown
at the decision cutoff (copy them from the case's frozen Jira source). --root limits the
entity list to the case's analysis root (for example python/ for Arrow).
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from semantic_target import load_target

from codeatlas.analyze.criteria import split_criteria
from codeatlas.analyze.semantic_corpus import ELIGIBLE_KINDS
from codeatlas.analyze.semantic_labels import entity_ref

ENTITY_COLUMNS = (
    "entity_id",
    "label_ref",
    "kind",
    "file",
    "start_line",
    "end_line",
    "is_test",
    "signature",
)
CRITERIA_COLUMNS = ("criterion_id", "key", "version_no", "criterion_text")
TEST_MARKERS = ("tests/", "test/", "/test_", "_test.py", "conftest.py")


def is_test_file(path: str) -> bool:
    name = "/" + path.replace("\\", "/")
    return name.rsplit("/", 1)[-1].startswith("test_") or any(m in name for m in TEST_MARKERS)


def _write(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="PATH@SHA of the case's head commit")
    parser.add_argument("--key", required=True, help="Jira key, e.g. AIRFLOW-1234")
    parser.add_argument("--version", type=int, default=1, help="requirement version at cutoff")
    parser.add_argument("--issue-text", type=Path, required=True)
    parser.add_argument("--root", default="", help="analysis root, e.g. python/")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    target = load_target(args.repo)
    root = args.root.strip("/")
    entities = [
        e
        for e in target.entities
        if e.kind in ELIGIBLE_KINDS and (not root or e.file.startswith(root + "/"))
    ]
    entity_rows = [
        {
            "entity_id": e.entity_id,
            "label_ref": entity_ref(e),
            "kind": e.kind,
            "file": e.file,
            "start_line": str(e.start_line),
            "end_line": str(e.end_line),
            "is_test": "1" if is_test_file(e.file) else "0",
            "signature": (e.signature or "").replace("\n", " "),
        }
        for e in sorted(entities, key=lambda e: (e.file, e.start_line, e.entity_id))
    ]
    criteria = split_criteria(
        requirement_key=args.key,
        version_no=args.version,
        description=args.issue_text.read_text(encoding="utf-8"),
    )
    criteria_rows = [
        {
            "criterion_id": c.criterion_id,
            "key": c.key,
            "version_no": str(c.version_no),
            "criterion_text": c.text,
        }
        for c in criteria
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    _write(args.out / "entities.csv", ENTITY_COLUMNS, entity_rows)
    _write(args.out / "criteria.csv", CRITERIA_COLUMNS, criteria_rows)
    tests = sum(r["is_test"] == "1" for r in entity_rows)
    print(f"repo {target.label} @ {target.pinned_sha}")
    print(f"entities.csv: {len(entity_rows)} entities ({tests} in test files)")
    print(f"criteria.csv: {len(criteria_rows)} criteria for {args.key} v{args.version}")
    if not criteria_rows:
        print("no criteria found in the issue text; check --issue-text")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
