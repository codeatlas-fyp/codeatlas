"""Score a semantic-run-v1 export against labels: recall@k, hit@k and MRR per ranker (CA-02).

Inputs are the two CSV files the team already exchanges:

- `predictions.csv` from `semantic_export` (one row per criterion, ranker and rank);
- a labels CSV with columns criterion_id, criterion_text, entity_id, where an empty
  entity_id means nothing in the repository should match (same file `calibrate_gate` reads).

Ranking metrics count only criteria that have at least one relevant entity. A no-match
criterion has no right answer to rank, so it is scored by the gate instead: does the gate
abstain on it? The gate table also shows how often a real match is abstained on (a missed
link) and how precise `accept` is.

The export keeps 10 rows per ranker but only the top-k fused candidates (5 by default), so
for "fused" recall@10 equals recall@5.

The numbers are a function of the two files only, so anyone can recompute them.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from codeatlas.analyze.semantic_gate import DevCriterion

EVAL_VERSION = "semantic-eval@1"
KS = (1, 5, 10)
RANKER_ORDER = ("bm25", "minilm", "codet5p", "fused")


@dataclass(frozen=True)
class RankerScore:
    ranker: str
    criteria: int  # labelled criteria with at least one relevant entity
    recall: dict[int, float]  # k -> mean recall@k
    hit: dict[int, float]  # k -> share of criteria with a relevant entity in the top k
    mrr: float


@dataclass(frozen=True)
class GateScore:
    match_criteria: int
    no_match_criteria: int
    no_match_abstained: int  # good: "no confident match" where there is none
    match_abstained: int  # bad: a real match the gate hid
    accepted: int
    accepted_correct: int  # accept and the fused top-1 is relevant


@dataclass(frozen=True)
class Evaluation:
    run_ids: tuple[str, ...]
    rankers: tuple[RankerScore, ...]
    gate: GateScore
    unlabelled: tuple[str, ...]  # criterion ids predicted but not in the labels
    missing: tuple[str, ...]  # labelled criterion ids with no predictions


def read_predictions(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def evaluate(rows: Iterable[dict[str, str]], labels: list[DevCriterion]) -> Evaluation:
    rows = list(rows)
    relevant = {d.criterion_id: d.relevant for d in labels}
    ranked: dict[tuple[str, str], list[tuple[int, str]]] = {}
    gates: dict[str, str] = {}
    for row in rows:
        cid = row["criterion_id"]
        ranked.setdefault((row["ranker"], cid), []).append((int(row["rank"]), row["entity_id"]))
        gates[cid] = row.get("gate", "")
    predicted = {cid for _, cid in ranked}

    scores: list[RankerScore] = []
    present = {name for name, _ in ranked}
    for name in [n for n in RANKER_ORDER if n in present] + sorted(present - set(RANKER_ORDER)):
        recalls: dict[int, list[float]] = {k: [] for k in KS}
        hits: dict[int, list[float]] = {k: [] for k in KS}
        reciprocal: list[float] = []
        for d in labels:
            if not d.relevant:
                continue
            order = [e for _, e in sorted(ranked.get((name, d.criterion_id), []))]
            first = next((i for i, e in enumerate(order, start=1) if e in d.relevant), None)
            reciprocal.append(1.0 / first if first else 0.0)
            for k in KS:
                found = len(d.relevant.intersection(order[:k]))
                recalls[k].append(found / len(d.relevant))
                hits[k].append(1.0 if found else 0.0)
        scores.append(
            RankerScore(
                ranker=name,
                criteria=len(reciprocal),
                recall={k: _mean(v) for k, v in recalls.items()},
                hit={k: _mean(v) for k, v in hits.items()},
                mrr=_mean(reciprocal),
            )
        )

    fused_top = {
        cid: min(items)[1] for (name, cid), items in ranked.items() if name == "fused" and items
    }
    labelled = [d for d in labels if d.criterion_id in predicted]
    match = [d for d in labelled if d.relevant]
    no_match = [d for d in labelled if not d.relevant]
    accepted = [d for d in labelled if gates.get(d.criterion_id) == "accept"]
    gate = GateScore(
        match_criteria=len(match),
        no_match_criteria=len(no_match),
        no_match_abstained=sum(gates.get(d.criterion_id) == "abstain" for d in no_match),
        match_abstained=sum(gates.get(d.criterion_id) == "abstain" for d in match),
        accepted=len(accepted),
        accepted_correct=sum(fused_top.get(d.criterion_id) in d.relevant for d in accepted),
    )
    return Evaluation(
        run_ids=tuple(sorted({row["run_id"] for row in rows})),
        rankers=tuple(scores),
        gate=gate,
        unlabelled=tuple(sorted(predicted - relevant.keys())),
        missing=tuple(sorted(relevant.keys() - predicted)),
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def markdown(result: Evaluation) -> str:
    """The evaluation as Markdown tables, ready for the report."""
    head = "| ranker | criteria | " + " | ".join(f"recall@{k}" for k in KS)
    head += " | " + " | ".join(f"hit@{k}" for k in KS) + " | MRR |"
    lines = [
        f"Run: {', '.join(result.run_ids) or '-'} ({EVAL_VERSION})",
        "",
        head,
        "|" + "---|" * (3 + 2 * len(KS)),
    ]
    for s in result.rankers:
        cells = [f"{s.recall[k]:.3f}" for k in KS] + [f"{s.hit[k]:.3f}" for k in KS]
        lines.append(f"| {s.ranker} | {s.criteria} | " + " | ".join(cells) + f" | {s.mrr:.3f} |")
    g = result.gate
    lines += [
        "",
        "| gate | count |",
        "|---|---|",
        f"| no-match criteria abstained | {g.no_match_abstained} / {g.no_match_criteria} |",
        f"| real matches abstained (missed) | {g.match_abstained} / {g.match_criteria} |",
        f"| accepted, top-1 correct | {g.accepted_correct} / {g.accepted} |",
    ]
    if result.missing:
        lines += ["", f"Labelled but not predicted: {', '.join(result.missing)}"]
    if result.unlabelled:
        lines += ["", f"Predicted but not labelled (ignored): {', '.join(result.unlabelled)}"]
    return "\n".join(lines) + "\n"


def as_dict(result: Evaluation) -> dict[str, object]:
    return {
        "eval_version": EVAL_VERSION,
        "run_ids": list(result.run_ids),
        "rankers": [
            {
                "ranker": s.ranker,
                "criteria": s.criteria,
                "recall": {str(k): v for k, v in s.recall.items()},
                "hit": {str(k): v for k, v in s.hit.items()},
                "mrr": s.mrr,
            }
            for s in result.rankers
        ],
        "gate": result.gate.__dict__,
        "unlabelled": list(result.unlabelled),
        "missing": list(result.missing),
    }


__all__ = [
    "EVAL_VERSION",
    "KS",
    "Evaluation",
    "GateScore",
    "RankerScore",
    "as_dict",
    "evaluate",
    "markdown",
    "read_predictions",
]
