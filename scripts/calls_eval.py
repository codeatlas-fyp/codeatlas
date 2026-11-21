"""CALLS evaluation on hand-labelled call sites (SG-07). See docs/calls-evaluation.md.

Usage (run from the codeatlas folder; ROOT is a checkout of the repository being measured):

    uv run python scripts/calls_eval.py sample --root ROOT --include pkg --repo owner/name \
        --revision SHA --per-stratum 10 --seed 2026 --out sheet.csv
    uv run python scripts/calls_eval.py agree --first a.csv --second b.csv
    uv run python scripts/calls_eval.py score --root ROOT --include pkg --labels settled.csv \
        --strata sheet_strata.csv --out report.md

`sample` writes a blind sheet (the call and its line, never CodeAtlas's answer or the stratum)
and a separate strata file next to it. `score` reruns the resolver on the same source and
compares it with the settled labels, per stratum when the strata file is given.
"""

import argparse
import csv
import sys
from pathlib import Path

import codeatlas.analyze.calls_eval as calls_eval
from codeatlas.analyze.calls import EXTRACTOR_VERSION, call_site_strata, resolve_call_sites
from codeatlas.analyze.code import extract_entities
from codeatlas.schema import CodeEntity


def load_sources(root: Path, include: list[str]) -> dict[str, bytes]:
    """Python files under `root/<include>`, keyed by POSIX path relative to `root`."""
    sources: dict[str, bytes] = {}
    for part in include or ["."]:
        base = (root / part).resolve()
        if not base.is_dir():
            raise SystemExit(f"not a folder: {base}")
        for path in sorted(base.rglob("*.py")):
            rel = path.resolve().relative_to(root.resolve())
            if any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                continue
            sources[rel.as_posix()] = path.read_bytes()
    if not sources:
        raise SystemExit("no Python files found")
    return sources


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def entities_of(sources: dict[str, bytes]) -> list[CodeEntity]:
    return [
        e for path, src in sources.items() for e in extract_entities(file_path=path, source=src)
    ]


def write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def strata_path(sheet: Path) -> Path:
    return sheet.with_name(sheet.stem + "_strata.csv")


def cmd_sample(args: argparse.Namespace) -> int:
    sources = load_sources(Path(args.root), args.include)
    sites = calls_eval.find_call_sites(sources)
    strata = calls_eval.stratify(
        sites, call_site_strata(entities=entities_of(sources), file_sources=sources)
    )
    sample = calls_eval.sample_stratified(sites, strata, args.per_stratum, args.seed)
    out = Path(args.out)
    rows = calls_eval.sheet_rows(sample.sites, args.repo, args.revision)
    write_csv(out, calls_eval.SHEET_COLUMNS, rows)
    write_csv(
        strata_path(out),
        calls_eval.STRATA_COLUMNS,
        [{"site_id": s.site_id, "stratum": strata[s.site_id]} for s in sample.sites],
    )
    print(f"{len(sample.sites)} of {len(sites)} call sites, {len(sources)} files, seed {args.seed}")
    for stratum, available in sample.available.items():
        print(f"  {stratum}: {sample.taken[stratum]} of {available}")
    for stratum, missing in sample.shortfall(args.per_stratum).items():
        print(f"  SHORTFALL {stratum}: {missing} fewer than {args.per_stratum}")
    print(f"sheet -> {out}; strata (keep away from annotators) -> {strata_path(out)}")
    return 0


def cmd_agree(args: argparse.Namespace) -> int:
    first = calls_eval.parse_labels(read_csv(Path(args.first)))
    second = calls_eval.parse_labels(read_csv(Path(args.second)))
    result = calls_eval.agreement(first, second)
    rate = f"{result.rate:.3f}" if result.rate is not None else "n/a"
    print(f"agreement {result.agreed} / {result.compared} = {rate}")
    for sid, a, b in result.disagreements:
        print(f"  {sid}: {a} | {b}")
    if result.only_in_one:
        print(f"labelled in only one sheet: {', '.join(result.only_in_one)}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    root = Path(args.root)
    sources = load_sources(root, args.include)
    labels = calls_eval.parse_labels(read_csv(Path(args.labels)))
    sites = resolve_call_sites(entities=entities_of(sources), file_sources=sources)
    strata = None
    if args.strata:
        strata = {r["site_id"]: r["stratum"] for r in read_csv(Path(args.strata))}
    report = calls_eval.score(
        calls_eval.predictions_by_site(sites),
        labels,
        (s.site_id for s in calls_eval.find_call_sites(sources)),
        strata,
    )
    text = calls_eval.render_report(
        report,
        title="CALLS evaluation",
        details=[
            f"Source: `{root.name}`, folders {', '.join(args.include or ['.'])}",
            f"Labels: `{Path(args.labels).name}`",
            f"Resolver: `{EXTRACTOR_VERSION}`",
        ],
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"report -> {args.out}")
    else:
        print(text)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sample = sub.add_parser("sample", help="write a blind annotation sheet")
    sample.add_argument("--root", required=True)
    sample.add_argument("--include", action="append", default=[])
    sample.add_argument("--repo", required=True)
    sample.add_argument("--revision", required=True)
    sample.add_argument("--per-stratum", type=int, default=10)
    sample.add_argument("--seed", type=int, default=2026)
    sample.add_argument("--out", required=True)
    sample.set_defaults(run=cmd_sample)

    agree = sub.add_parser("agree", help="compare two annotators' sheets")
    agree.add_argument("--first", required=True)
    agree.add_argument("--second", required=True)
    agree.set_defaults(run=cmd_agree)

    score = sub.add_parser("score", help="score the resolver against settled labels")
    score.add_argument("--root", required=True)
    score.add_argument("--include", action="append", default=[])
    score.add_argument("--labels", required=True)
    score.add_argument("--strata")
    score.add_argument("--out")
    score.set_defaults(run=cmd_score)

    args = parser.parse_args(argv)
    result: int = args.run(args)
    return result


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
