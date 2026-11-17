"""Score semantic runs against labels without a fixed top-k cut-off (CA-02).

Top 5 is what the product shows (bundle candidates, C9). It is not how retrieval is judged
here. Each question gets its own measure:

1. Where does the right code land? For every labelled criterion with a right answer, the
   rank of its first relevant entity in each ranker's FULL ranking (`rankings.csv`): median
   rank, MRR with no cut-off, and recall/hit at k = 1, 5, 10, 20, 50 (a recall curve).
   BM25 ranks only entities sharing a word with the criterion; a relevant entity it never
   ranked counts as "not ranked" (a miss at every k, reciprocal rank 0).
2. Does the system stay quiet when nothing matches? For no-match criteria: the share the
   gate abstained on. The gate decides by score thresholds, not by list position.
3. Is the gate's confidence trustworthy? For real matches: the share it wrongly abstained
   on, and the precision of `accept` (the fused top-1 is relevant).
4. How sure are we? Every rate comes with its count and a 95% Wilson interval, and the
   ranking measures are repeated per repository, because cases in one repository are
   not independent.

Inputs are CSV files only (`rankings.csv` from `semantic_export`, plus a labels CSV with
criterion_id, criterion_text, entity_id), so anyone can recompute the numbers. An older
run without `rankings.csv` can be scored from `predictions.csv`, cut at its top rows; the
result then says so.
"""

from __future__ import annotations

import csv
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codeatlas.analyze.semantic_gate import DevCriterion

EVAL_VERSION = "semantic-eval@2"
KS = (1, 5, 10, 20, 50)
RANKER_ORDER = ("bm25", "minilm", "codet5p", "fused")
Z = 1.96


@dataclass(frozen=True)
class Rate:
    hits: int
    n: int

    @property
    def value(self) -> float | None:
        return self.hits / self.n if self.n else None

    def wilson(self) -> tuple[float, float] | None:
        """95% Wilson score interval; None when n = 0 ("not estimable")."""
        if not self.n:
            return None
        p, n = self.hits / self.n, self.n
        centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
        half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
        return max(0.0, centre - half), min(1.0, centre + half)

    def text(self) -> str:
        interval = self.wilson()
        if interval is None:
            return f"{self.hits}/{self.n} (not estimable)"
        return (
            f"{self.hits}/{self.n} = {self.hits / self.n:.2f} "
            f"[{interval[0]:.2f}, {interval[1]:.2f}]"
        )


@dataclass(frozen=True)
class RankerScore:
    ranker: str
    repo: str  # "all" or one repository
    criteria: int  # labelled criteria with at least one relevant entity
    first_ranks: tuple[int | None, ...]  # rank of the first relevant entity; None = not ranked
    recall: dict[int, float]  # k -> mean share of relevant entities in the top k
    hit: dict[int, Rate]  # k -> criteria with a relevant entity in the top k
    mrr: float

    @property
    def not_ranked(self) -> int:
        return sum(r is None for r in self.first_ranks)

    @property
    def median_rank(self) -> float | None:
        """Median rank of the first relevant entity; None if it falls on "not ranked"."""
        if not self.first_ranks:
            return None
        values = [math.inf if r is None else float(r) for r in self.first_ranks]
        median = statistics.median(values)
        return None if math.isinf(median) else median


@dataclass(frozen=True)
class GateScore:
    no_match_abstained: Rate  # good: quiet where nothing matches
    match_abstained: Rate  # bad: a real match the gate hid
    accept_correct: Rate  # accept and the fused top-1 is relevant


@dataclass(frozen=True)
class Evaluation:
    run_ids: tuple[str, ...]
    source: str  # "rankings.csv (full)" or "predictions.csv (top rows only)"
    rankers: tuple[RankerScore, ...]  # repo == "all"
    per_repo: tuple[RankerScore, ...]
    gate: GateScore
    unlabelled: tuple[str, ...]  # criterion ids predicted but not in the labels
    missing: tuple[str, ...]  # labelled criterion ids with no predictions


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


read_predictions = read_rows  # name kept for older callers


def evaluate(
    rows: Iterable[dict[str, str]],
    labels: list[DevCriterion],
    *,
    source: str = "rankings.csv (full)",
) -> Evaluation:
    rows = list(rows)
    ranked: dict[tuple[str, str], list[tuple[int, str]]] = {}
    gates: dict[str, str] = {}
    repo_of: dict[str, str] = {}
    for row in rows:
        cid = row["criterion_id"]
        ranked.setdefault((row["ranker"], cid), []).append((int(row["rank"]), row["entity_id"]))
        gates[cid] = row.get("gate", "")
        repo_of[cid] = row.get("repo", "") or "-"
    orders = {key: [e for _, e in sorted(items)] for key, items in ranked.items()}
    predicted = {cid for _, cid in orders}
    labelled = [d for d in labels if d.criterion_id in predicted]
    present = {name for name, _ in orders}
    names = [n for n in RANKER_ORDER if n in present] + sorted(present - set(RANKER_ORDER))

    def score(name: str, repo: str, items: list[DevCriterion]) -> RankerScore:
        firsts: list[int | None] = []
        recalls: dict[int, list[float]] = {k: [] for k in KS}
        hits: dict[int, int] = dict.fromkeys(KS, 0)
        for d in items:
            order = orders.get((name, d.criterion_id), [])
            first = next((i for i, e in enumerate(order, start=1) if e in d.relevant), None)
            firsts.append(first)
            for k in KS:
                found = len(d.relevant.intersection(order[:k]))
                recalls[k].append(found / len(d.relevant))
                hits[k] += 1 if found else 0
        return RankerScore(
            ranker=name,
            repo=repo,
            criteria=len(items),
            first_ranks=tuple(firsts),
            recall={k: _mean(v) for k, v in recalls.items()},
            hit={k: Rate(hits[k], len(items)) for k in KS},
            mrr=_mean([1.0 / r if r else 0.0 for r in firsts]),
        )

    match = [d for d in labelled if d.relevant]
    no_match = [d for d in labelled if not d.relevant]
    repos = sorted({repo_of[d.criterion_id] for d in match})
    overall = tuple(score(n, "all", match) for n in names)
    per_repo = tuple(
        score(n, repo, [d for d in match if repo_of[d.criterion_id] == repo])
        for repo in repos
        for n in names
    )

    fused = {cid: order[0] for (name, cid), order in orders.items() if name == "fused" and order}
    accepted = [d for d in labelled if gates.get(d.criterion_id) == "accept"]
    gate = GateScore(
        no_match_abstained=Rate(
            sum(gates.get(d.criterion_id) == "abstain" for d in no_match), len(no_match)
        ),
        match_abstained=Rate(
            sum(gates.get(d.criterion_id) == "abstain" for d in match), len(match)
        ),
        accept_correct=Rate(
            sum(fused.get(d.criterion_id) in d.relevant for d in accepted), len(accepted)
        ),
    )
    relevant_ids = {d.criterion_id for d in labels}
    return Evaluation(
        run_ids=tuple(sorted({row["run_id"] for row in rows})),
        source=source,
        rankers=overall,
        per_repo=per_repo,
        gate=gate,
        unlabelled=tuple(sorted(predicted - relevant_ids)),
        missing=tuple(sorted(relevant_ids - predicted)),
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _median_text(score: RankerScore) -> str:
    median = score.median_rank
    if median is None:
        return "not ranked" if score.first_ranks else "-"
    return f"{median:g}"


# ---- views ------------------------------------------------------------------------------------


def markdown(result: Evaluation) -> str:
    """The evaluation as Markdown tables, ready for the report."""
    lines = [
        f"Runs: {', '.join(result.run_ids) or '-'} ({EVAL_VERSION}; ranks from {result.source})",
        "",
        "Where the first relevant entity lands in each ranker's full ranking:",
        "",
        "| ranker | criteria | median rank | not ranked | MRR | "
        + " | ".join(f"recall@{k}" for k in KS)
        + " |",
        "|" + "---|" * (5 + len(KS)),
    ]
    for s in result.rankers:
        cells = " | ".join(f"{s.recall[k]:.3f}" for k in KS)
        lines.append(
            f"| {s.ranker} | {s.criteria} | {_median_text(s)} | {s.not_ranked} | "
            f"{s.mrr:.3f} | {cells} |"
        )
    lines += [
        "",
        "Criteria with a relevant entity within the first k (count, rate, 95% Wilson interval):",
        "",
        "| ranker | " + " | ".join(f"hit@{k}" for k in KS) + " |",
        "|" + "---|" * (1 + len(KS)),
    ]
    for s in result.rankers:
        lines.append(f"| {s.ranker} | " + " | ".join(s.hit[k].text() for k in KS) + " |")
    if len({s.repo for s in result.per_repo}) > 1:
        lines += [
            "",
            "Per repository:",
            "",
            "| repo | ranker | criteria | median rank | MRR | hit@10 |",
            "|---|---|---|---|---|---|",
        ]
        for s in result.per_repo:
            lines.append(
                f"| {s.repo} | {s.ranker} | {s.criteria} | {_median_text(s)} | "
                f"{s.mrr:.3f} | {s.hit[10].text()} |"
            )
    g = result.gate
    lines += [
        "",
        "Gate (decided by score thresholds, not list position):",
        "",
        "| question | count, rate, 95% Wilson interval |",
        "|---|---|",
        f"| no-match criteria abstained (higher is better) | {g.no_match_abstained.text()} |",
        f"| real matches abstained (lower is better) | {g.match_abstained.text()} |",
        f"| accepted with a correct top-1 (precision) | {g.accept_correct.text()} |",
    ]
    if result.missing:
        lines += ["", f"Labelled but not predicted: {', '.join(result.missing)}"]
    if result.unlabelled:
        lines += ["", f"Predicted but not labelled (ignored): {', '.join(result.unlabelled)}"]
    return "\n".join(lines) + "\n"


def _rate_dict(rate: Rate) -> dict[str, object]:
    interval = rate.wilson()
    return {
        "hits": rate.hits,
        "n": rate.n,
        "rate": rate.value,
        "wilson95": list(interval) if interval else None,
    }


def _score_dict(s: RankerScore) -> dict[str, object]:
    return {
        "ranker": s.ranker,
        "repo": s.repo,
        "criteria": s.criteria,
        "median_rank": s.median_rank,
        "not_ranked": s.not_ranked,
        "first_ranks": list(s.first_ranks),
        "mrr": s.mrr,
        "recall": {str(k): v for k, v in s.recall.items()},
        "hit": {str(k): _rate_dict(r) for k, r in s.hit.items()},
    }


def as_dict(result: Evaluation) -> dict[str, object]:
    return {
        "eval_version": EVAL_VERSION,
        "run_ids": list(result.run_ids),
        "source": result.source,
        "ks": list(KS),
        "rankers": [_score_dict(s) for s in result.rankers],
        "per_repo": [_score_dict(s) for s in result.per_repo],
        "gate": {
            "no_match_abstained": _rate_dict(result.gate.no_match_abstained),
            "match_abstained": _rate_dict(result.gate.match_abstained),
            "accept_correct": _rate_dict(result.gate.accept_correct),
        },
        "unlabelled": list(result.unlabelled),
        "missing": list(result.missing),
    }


def recall_curve_csv(result: Evaluation) -> str:
    lines = ["ranker," + ",".join(f"recall@{k}" for k in KS)]
    for s in result.rankers:
        lines.append(s.ranker + "," + ",".join(f"{s.recall[k]:.4f}" for k in KS))
    return "\n".join(lines) + "\n"


_COLOURS = {"bm25": "#2563eb", "minilm": "#d97706", "codet5p": "#059669", "fused": "#7c3aed"}


def recall_curve_svg(result: Evaluation) -> str:
    """A self-contained SVG line chart of recall@k per ranker (no plotting library needed)."""
    width, height, left, right, top, bottom = 560, 340, 56, 120, 24, 48
    plot_w, plot_h = width - left - right, height - top - bottom

    def x(i: int) -> float:
        return left + plot_w * i / (len(KS) - 1)

    def y(v: float) -> float:
        return top + plot_h * (1 - v)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line x1="{left}" y1="{y(tick):.1f}" x2="{left + plot_w}" y2="{y(tick):.1f}" '
            'stroke="#e5e7eb"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y(tick) + 4:.1f}" text-anchor="end" fill="#374151">'
            f"{tick:.2f}</text>"
        )
    for i, k in enumerate(KS):
        parts.append(
            f'<text x="{x(i):.1f}" y="{top + plot_h + 18}" text-anchor="middle" '
            f'fill="#374151">{k}</text>'
        )
    parts.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{height - 8}" text-anchor="middle" '
        'fill="#111827">k (rank cut-off)</text>'
    )
    parts.append(
        f'<text x="14" y="{top + plot_h / 2:.1f}" text-anchor="middle" fill="#111827" '
        f'transform="rotate(-90 14 {top + plot_h / 2:.1f})">recall@k</text>'
    )
    for row, s in enumerate(result.rankers):
        colour = _COLOURS.get(s.ranker, "#6b7280")
        points = " ".join(f"{x(i):.1f},{y(s.recall[k]):.1f}" for i, k in enumerate(KS))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2"/>'
        )
        for i, k in enumerate(KS):
            parts.append(
                f'<circle cx="{x(i):.1f}" cy="{y(s.recall[k]):.1f}" r="3" fill="{colour}"/>'
            )
        ly = top + 12 + row * 20
        parts.append(
            f'<line x1="{left + plot_w + 16}" y1="{ly}" x2="{left + plot_w + 36}" y2="{ly}" '
            f'stroke="{colour}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{left + plot_w + 42}" y="{ly + 4}" fill="#111827">{s.ranker}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


__all__ = [
    "EVAL_VERSION",
    "KS",
    "Evaluation",
    "GateScore",
    "RankerScore",
    "Rate",
    "as_dict",
    "evaluate",
    "markdown",
    "read_predictions",
    "read_rows",
    "recall_curve_csv",
    "recall_curve_svg",
]
