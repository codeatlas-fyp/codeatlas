"""Accept / review / abstain gate for semantic candidates, calibrated on a dev set (CA-02).

A ranker always returns a top entity, even when nothing in the repository matches. The gate
decides, per criterion, whether that top entity is trustworthy:

- `accept`: the gate ranker's top cosine is at or above the accept threshold, and at least
  `min_agree` rankers have that same entity in their top 5;
- `review`: the top cosine is at or above the review threshold, but not enough for accept;
- `abstain`: below the review threshold, so CodeAtlas says "no confident match" instead of
  guessing (the no-match gate from the CA-02 guide);
- `unavailable`: no ranker could run at all.

The gate ranker is the first ranker in `calibration.order` that ran and has thresholds;
when the preferred one (CodeT5+) is unavailable, the next one is used and the reason says so.
BM25 alone never gives `accept`: its scores have no fixed scale to threshold.

Thresholds come from `calibrate`, which fits them on labelled dev criteria and is stored as a
versioned JSON file (`GateCalibration`). The gate output feeds C9 as advice only: a gate
decision never blocks a verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from codeatlas.analyze.rankers import RankerOutput
from codeatlas.schema import CriterionGate, SemanticCandidate

GATE_VERSION = "gate@1"
AGREE_TOP_N = 5


@dataclass(frozen=True)
class Thresholds:
    accept: float
    review: float


@dataclass(frozen=True)
class GateCalibration:
    calibration_id: str
    provisional: bool  # True until fitted on the labelled dataset
    order: tuple[str, ...]  # gate rankers, preferred first
    thresholds: dict[str, Thresholds]
    min_agree: int = 2
    dev_set: str | None = None  # file name of the labels used
    dev_set_sha256: str | None = None
    n_criteria: int = 0
    report: dict[str, dict[str, float]] = field(default_factory=dict)  # per ranker metrics

    def to_json(self) -> str:
        data = asdict(self)
        data["order"] = list(self.order)
        for limits in data["thresholds"].values():  # +inf ("never accept") is stored as null
            for key, value in limits.items():
                limits[key] = None if math.isinf(value) else value
        return json.dumps(data, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> GateCalibration:
        data = json.loads(text)
        data["order"] = tuple(data["order"])
        data["thresholds"] = {
            name: Thresholds(**{k: math.inf if v is None else v for k, v in limits.items()})
            for name, limits in data["thresholds"].items()
        }
        return cls(**data)


# Used only until a calibration file exists. Conservative on purpose: little is accepted.
PROVISIONAL = GateCalibration(
    calibration_id="gate@provisional-0",
    provisional=True,
    order=("codet5p", "minilm"),
    thresholds={"codet5p": Thresholds(0.60, 0.40), "minilm": Thresholds(0.60, 0.40)},
)
DEFAULT_CALIBRATION_FILE = Path(__file__).with_name("gate_calibration.json")


def load_calibration(path: Path = DEFAULT_CALIBRATION_FILE) -> GateCalibration:
    if not path.is_file():
        return PROVISIONAL
    return GateCalibration.from_json(path.read_text(encoding="utf-8"))


# ---- deciding ---------------------------------------------------------------------------------


def decide(
    criterion_ids: list[str],
    outputs: list[RankerOutput],
    calibration: GateCalibration,
) -> list[CriterionGate]:
    available: dict[str, RankerOutput] = {o.name: o for o in outputs if o.state != "unavailable"}
    gate_ranker = next(
        (available[n] for n in calibration.order if n in available and n in calibration.thresholds),
        None,
    )
    out: list[CriterionGate] = []
    for cid in criterion_ids:
        if not available:
            out.append(
                CriterionGate(
                    criterion_id=cid,
                    gate="unavailable",
                    top_score=None,
                    reason="no ranker could run",
                )
            )
            continue
        if gate_ranker is None:
            has_hit = any(o.rankings.get(cid) for o in available.values())
            out.append(
                CriterionGate(
                    criterion_id=cid,
                    gate="review" if has_hit else "abstain",
                    top_score=None,
                    reason="no calibrated dense ranker ran; BM25 alone is never accepted",
                )
            )
            continue
        out.append(_decide_one(cid, gate_ranker, available, calibration))
    return out


def _decide_one(
    cid: str,
    ranker: RankerOutput,
    available: dict[str, RankerOutput],
    calibration: GateCalibration,
) -> CriterionGate:
    ranking = ranker.rankings.get(cid, [])
    note = "" if ranker.name == calibration.order[0] else f" (gate ranker {ranker.name}: "
    if note:
        note += f"{calibration.order[0]} unavailable)"
    if not ranking:
        return CriterionGate(
            criterion_id=cid, gate="abstain", top_score=None, reason="no candidates" + note
        )
    top = ranking[0]
    score = round(top.score, 4)
    limits = calibration.thresholds[ranker.name]
    agree = sum(
        1
        for o in available.values()
        if any(r.entity_id == top.entity_id for r in o.rankings.get(cid, [])[:AGREE_TOP_N])
    )
    gate: Literal["accept", "review", "abstain"]
    if score >= limits.accept and agree >= calibration.min_agree:
        gate, reason = "accept", f"score {score} >= {limits.accept}, {agree} rankers agree"
    elif score >= limits.review:
        why = "rankers disagree" if score >= limits.accept else f"score below {limits.accept}"
        gate, reason = "review", f"score {score}; {why}"
    else:
        gate, reason = "abstain", f"score {score} < {limits.review}: no confident match"
    return CriterionGate(criterion_id=cid, gate=gate, top_score=score, reason=reason + note)


def apply_gate(
    candidates: list[SemanticCandidate], gates: list[CriterionGate]
) -> list[SemanticCandidate]:
    """Stamp each candidate with its criterion's gate (None when the gate is unavailable)."""
    by_id = {g.criterion_id: g.gate for g in gates}
    out: list[SemanticCandidate] = []
    for cand in candidates:
        gate = by_id.get(cand.criterion_id)
        stamped = gate if gate in ("accept", "review", "abstain") else None
        out.append(cand.model_copy(update={"gate": stamped}))
    return out


# ---- calibrating ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DevCriterion:
    criterion_id: str
    text: str
    relevant: frozenset[str]  # empty = nothing in the repository should match


def read_dev_labels(path: Path) -> list[DevCriterion]:
    """CSV with columns criterion_id, criterion_text, entity_id (empty for no-match rows)."""
    import csv

    texts: dict[str, str] = {}
    relevant: dict[str, set[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cid = row["criterion_id"].strip()
            texts[cid] = row["criterion_text"]
            relevant.setdefault(cid, set())
            if row.get("entity_id", "").strip():
                relevant[cid].add(row["entity_id"].strip())
    return [DevCriterion(cid, texts[cid], frozenset(relevant[cid])) for cid in sorted(texts)]


def calibrate(
    dev: list[DevCriterion],
    outputs: list[RankerOutput],
    *,
    calibration_id: str,
    dev_set: Path | None = None,
    target_precision: float = 0.9,
    abstain_max_hit: float = 0.2,
    min_support: int = 3,
    provisional: bool = True,
) -> GateCalibration:
    """Fit accept/review thresholds for each dense ranker that ran.

    accept = the lowest score t such that, among dev criteria whose top score is >= t (at
    least `min_support` of them), the top entity is correct at least `target_precision` of
    the time. If no t reaches that, accept is +inf: this ranker never auto-accepts.

    review = the highest score t (<= accept) such that, among dev criteria scoring below t,
    the right entity is in the top 5 at most `abstain_max_hit` of the time: below it,
    showing candidates would mostly show wrong code, so the gate abstains.
    """
    thresholds: dict[str, Thresholds] = {}
    report: dict[str, dict[str, float]] = {}
    order = tuple(n for n in ("codet5p", "minilm") if any(o.name == n for o in outputs))
    for output in outputs:
        if output.name not in order or output.state == "unavailable":
            continue
        rows = []
        for item in dev:
            ranking = output.rankings.get(item.criterion_id, [])
            if not ranking:
                continue
            top1 = ranking[0].entity_id in item.relevant
            hit5 = any(r.entity_id in item.relevant for r in ranking[:5])
            rows.append((ranking[0].score, top1, hit5))
        accept = _fit_accept(rows, target_precision, min_support)
        review = min(_fit_review(rows, abstain_max_hit), accept)
        thresholds[output.name] = Thresholds(round(accept, 4), round(review, 4))
        report[output.name] = _report(rows, accept, review)
    digest = hashlib.sha256(dev_set.read_bytes()).hexdigest() if dev_set else None
    return GateCalibration(
        calibration_id=calibration_id,
        provisional=provisional,
        order=tuple(n for n in order if n in thresholds),
        thresholds=thresholds,
        dev_set=dev_set.name if dev_set else None,
        dev_set_sha256=digest,
        n_criteria=len(dev),
        report=report,
    )


def _fit_accept(rows: list[tuple[float, bool, bool]], target: float, support: int) -> float:
    for t in sorted({s for s, _, _ in rows}):
        above = [top1 for s, top1, _ in rows if s >= t]
        if len(above) >= support and sum(above) / len(above) >= target:
            return t
    return math.inf


def _fit_review(rows: list[tuple[float, bool, bool]], max_hit: float) -> float:
    best = min((s for s, _, _ in rows), default=0.0)
    for t in sorted({s for s, _, _ in rows}):
        below = [hit5 for s, _, hit5 in rows if s < t]
        if below and sum(below) / len(below) <= max_hit:
            best = t
    return best


def _report(rows: list[tuple[float, bool, bool]], accept: float, review: float) -> dict[str, float]:
    n = len(rows) or 1
    accepted = [r for r in rows if r[0] >= accept]
    abstained = [r for r in rows if r[0] < review]
    return {
        "criteria": float(len(rows)),
        "top1_accuracy": round(sum(r[1] for r in rows) / n, 4),
        "hit_at_5": round(sum(r[2] for r in rows) / n, 4),
        "accepted": float(len(accepted)),
        "accepted_precision": round(sum(r[1] for r in accepted) / len(accepted), 4)
        if accepted
        else 0.0,
        "abstained": float(len(abstained)),
        "abstained_with_hit": float(sum(r[2] for r in abstained)),
    }
