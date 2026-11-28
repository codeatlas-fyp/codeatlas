"""Run CA-02 on every frozen case in one go, check each run, then score them together (task 21).

    uv run python scripts/run_semantic_cases.py --repos-dir D:/FYP/codeatlas-eval/eval/repos
        --cases-dir D:/FYP/codeatlas-eval/eval/cases --out runs/sprint
    ... --labels semantic_labels.csv          (after gate 3: also score the runs)
    ... --case D:/FYP/codeatlas-eval/eval/cases/AF-01 --case ...   (instead of --cases-dir)

For each case folder (case.yaml plus packet/criteria.csv and packet/entities.csv) it:

1. reads the repository, head_at_merge and analysis_root from case.yaml, and the criteria
   from packet/criteria.csv, word for word;
2. builds the corpus from <repos-dir>/<repo>@head_at_merge under the analysis root and runs
   BM25, MiniLM and CodeT5+ (each model is loaded once for all cases; vectors come from the
   .codeatlas-cache/ that precompute_embeddings.py filled);
3. writes <out>/<case_id>/ (semantic-run-v1.json, predictions.csv, rankings.csv), run id =
   case id, repo = the repository name, so the evaluation can resample whole cases and
   report per repository;
4. checks that every model ranked exactly packet/entities.csv (universe_check.json).

A no-key case (case.yaml `jira_keys: [none]`, e.g. SP-03) has no requirement and so no packet:
it is listed in the summary as "no criteria" with its reason, and never ranked or scored. It
does not fail the run, and it still counts in the rule results, which this script does not
compute. A case with Jira keys but no packet is still an error (exit 1): build its packet.

Then it writes <out>/cases_summary.json and .md. With --labels (the adjudicated labels as a
CSV: criterion_id, criterion_text, entity_id) it scores only the cases whose check passed and
writes evaluation.md/.json and the recall curve to <out>; failed cases are listed, never scored.
Exit code: 0 all good, 3 when any case failed its check, 1 on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from frozen_case import FrozenCase, read_case
from semantic_target import ROOT, load_target

from codeatlas.analyze.semantic_eval import (
    as_dict,
    evaluate,
    markdown,
    read_rows,
    recall_curve_csv,
    recall_curve_svg,
)
from codeatlas.analyze.semantic_export import RANKINGS_NAME, write_export
from codeatlas.analyze.semantic_gate import load_calibration, read_dev_labels
from codeatlas.analyze.semantic_run import Loader, default_loaders, load_once, run_semantic
from codeatlas.analyze.semantic_universe import check_universe, read_entity_ids

CACHE = ROOT / ".codeatlas-cache"
UNIVERSE_NAME = "universe_check.json"
EXIT_CHECK_FAILED = 3


NO_CRITERIA = "no_criteria"
NO_KEY_REASON = "no Jira key (jira_keys: [none]), so no criteria to rank"


def find_cases(args: argparse.Namespace) -> tuple[list[FrozenCase], list[FrozenCase]]:
    """(cases to run, no-key cases to list as "no criteria")."""
    folders = list(args.case)
    if args.cases_dir is not None:
        folders += sorted(p for p in args.cases_dir.iterdir() if (p / "case.yaml").is_file())
    cases = [read_case(folder) for folder in folders]
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"a case id is given twice: {sorted(ids)}")
    no_key = [c for c in cases if c.no_key]
    to_run = [c for c in cases if not c.no_key]
    for case in to_run:
        for path in (case.criteria_csv, case.entities_csv):
            if not path.is_file():
                raise ValueError(f"{case.case_id}: no {path} (build the packet first)")
    return to_run, no_key


def no_criteria_row(case: FrozenCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "repo": case.repo_name,
        "head_at_merge": case.head_at_merge,
        "analysis_root": case.analysis_root or "/",
        "status": NO_CRITERIA,
        "reason": NO_KEY_REASON,
    }


def run_case(
    case: FrozenCase,
    args: argparse.Namespace,
    loaders: dict[str, Loader],
) -> dict[str, object]:
    target = load_target(case.target_spec(args.repos_dir), case.analysis_root)
    criteria = [d.as_criterion() for d in read_dev_labels(case.criteria_csv)]
    result = run_semantic(
        criteria=criteria,
        entities=target.entities,
        file_sources=target.sources,
        pinned_sha=target.pinned_sha,
        top_k=args.top_k,
        bm25_only=args.bm25_only,
        calibration=load_calibration(args.calibration) if args.calibration else None,
        loaders=loaders,
    )
    folder = args.out / case.case_id
    write_export(
        folder, result, criteria, run_id=case.case_id, repo=case.repo_name, top_n=args.top_n
    )
    check = check_universe(result, read_entity_ids(case.entities_csv))
    (folder / UNIVERSE_NAME).write_text(
        json.dumps(check.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    gates: dict[str, int] = {}
    for gate in result.gates:
        gates[gate.gate] = gates.get(gate.gate, 0) + 1
    return {
        "case_id": case.case_id,
        "repo": case.repo_name,
        "head_at_merge": case.head_at_merge,
        "analysis_root": case.analysis_root or "/",
        "status": "ran",
        "entities": check.corpus,
        "criteria": len(criteria),
        "rankers": {r.name: r.state for r in result.summary.rankers},
        "gates": dict(sorted(gates.items())),
        "universe_ok": check.ok,
        "missing": len(check.missing),
        "extra": len(check.extra),
    }


def summary_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "| case | repo | root | entities | criteria | entity list check | gates |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["status"] == NO_CRITERIA:
            lines.append(
                f"| {r['case_id']} | {r['repo']} | {r['analysis_root']} | - | 0 | "
                f"not run: {r['reason']} | - |"
            )
            continue
        check = "OK" if r["universe_ok"] else f"FAILED ({r['missing']} missing, {r['extra']} extra)"
        lines.append(
            f"| {r['case_id']} | {r['repo']} | {r['analysis_root']} | {r['entities']} | "
            f"{r['criteria']} | {check} | {r['gates']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, action="append", default=[], help="repeatable")
    parser.add_argument("--cases-dir", type=Path, default=None, help="every case folder in it")
    parser.add_argument("--repos-dir", type=Path, required=True, help="the repository clones")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "cases")
    parser.add_argument("--labels", type=Path, default=None, help="adjudicated labels CSV")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--calibration", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--bm25-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.case and args.cases_dir is None:
        parser.error("give --cases-dir or at least one --case")

    try:
        cases, no_key = find_cases(args)
    except ValueError as exc:
        print(exc)
        return 1
    rows: list[dict[str, object]] = []
    for case in no_key:
        print(f"[{case.case_id}] not run: {NO_KEY_REASON}")
        rows.append(no_criteria_row(case))
    loaders = load_once(default_loaders(args.cache)) if cases else {}
    for case in cases:
        print(
            f"[{case.case_id}] {case.repo_name} @ {case.head_at_merge[:8]} "
            f"root {case.analysis_root or '/'}"
        )
        row = run_case(case, args, loaders)
        print(
            f"   {row['entities']} entities, {row['criteria']} criteria, check "
            f"{'OK' if row['universe_ok'] else 'FAILED'}"
        )
        rows.append(row)

    rows.sort(key=lambda r: str(r["case_id"]))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cases_summary.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table = summary_markdown(rows)
    (args.out / "cases_summary.md").write_text(table, encoding="utf-8")
    print(table)
    ran = [r for r in rows if r["status"] == "ran"]
    failed = [str(r["case_id"]) for r in ran if not r["universe_ok"]]
    not_run = [str(r["case_id"]) for r in rows if r["status"] == NO_CRITERIA]

    if args.labels is not None:
        ok = [str(r["case_id"]) for r in ran if r["universe_ok"]]
        ranking_rows = [row for cid in ok for row in read_rows(args.out / cid / RANKINGS_NAME)]
        result = evaluate(ranking_rows, read_dev_labels(args.labels))
        text = markdown(result)
        if failed:
            text += f"\nNot scored (entity list check failed): {', '.join(failed)}\n"
        if not_run:
            text += f"\nNot run (no Jira key, so no criteria): {', '.join(not_run)}\n"
        (args.out / "evaluation.md").write_text(text, encoding="utf-8")
        data = as_dict(result) | {
            "labels": args.labels.name,
            "not_scored": failed,
            "no_criteria": not_run,
        }
        (args.out / "evaluation.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (args.out / "recall_curve.csv").write_text(recall_curve_csv(result), encoding="utf-8")
        (args.out / "recall_curve.svg").write_text(recall_curve_svg(result), encoding="utf-8")
        print(text)
    if failed:
        print(f"entity list check failed for {', '.join(failed)}: do not report their numbers")
        return EXIT_CHECK_FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main())
