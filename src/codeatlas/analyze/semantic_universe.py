"""Did every model rank the same code the labellers saw? (CA-02, dataset plan task 21)

Labellers judge each criterion against `packet/entities.csv`: every function, method and
class at the case's head commit under its analysis root. A semantic run is only comparable
with those labels if it ranked exactly that list. This module checks it:

- the run's corpus holds exactly the entity ids of entities.csv (none missing, none extra);
- each dense ranker (MiniLM, CodeT5+) ranked every corpus entity for every criterion, because
  a cosine always exists;
- BM25 ranked only corpus entities. It ranks only entities sharing a word with the criterion,
  so it may rank fewer; that is how BM25 works, not a mismatch.

A ranker that did not run (`unavailable`) is reported as such. When the check fails, the
case's numbers are not reported; the check's own report is.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from codeatlas.analyze.semantic_run import SemanticRunResult

DENSE = ("minilm", "codet5p")


@dataclass(frozen=True)
class RankerCoverage:
    name: str
    state: str  # the ranker's state in the run: ok, fallback or unavailable
    criteria: int
    short: tuple[str, ...]  # criteria where a dense ranker ranked fewer than all entities
    outside: tuple[str, ...]  # criteria where a ranker ranked an entity not in the corpus

    @property
    def ok(self) -> bool:
        return not self.short and not self.outside


@dataclass(frozen=True)
class UniverseCheck:
    expected: int  # entities in entities.csv
    corpus: int  # entities in the run's corpus
    missing: tuple[str, ...]  # in entities.csv, not in the corpus
    extra: tuple[str, ...]  # in the corpus, not in entities.csv
    rankers: tuple[RankerCoverage, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.extra and all(r.ok for r in self.rankers)

    def as_dict(self, sample: int = 20) -> dict[str, object]:
        return {
            "ok": self.ok,
            "expected_entities": self.expected,
            "corpus_entities": self.corpus,
            "missing": len(self.missing),
            "missing_sample": list(self.missing[:sample]),
            "extra": len(self.extra),
            "extra_sample": list(self.extra[:sample]),
            "rankers": [
                {
                    "name": r.name,
                    "state": r.state,
                    "criteria": r.criteria,
                    "ok": r.ok,
                    "short": list(r.short),
                    "outside": list(r.outside),
                }
                for r in self.rankers
            ],
        }


def read_entity_ids(path: Path) -> list[str]:
    """The entity_id column of a packet's entities.csv (written by prepare_case.py)."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "entity_id" not in reader.fieldnames:
            raise ValueError(f"{path} has no entity_id column")
        return [row["entity_id"] for row in reader]


def check_universe(result: SemanticRunResult, expected_ids: list[str]) -> UniverseCheck:
    corpus_ids = {doc.entity_id for doc in result.corpus.docs}
    expected = set(expected_ids)
    coverage: list[RankerCoverage] = []
    for output in result.outputs:
        short: list[str] = []
        outside: list[str] = []
        for cid, ranking in sorted(output.rankings.items()):
            ranked = {item.entity_id for item in ranking}
            if ranked - corpus_ids:
                outside.append(cid)
            if output.name in DENSE and ranked != corpus_ids:
                short.append(cid)
        coverage.append(
            RankerCoverage(
                name=output.name,
                state=output.state,
                criteria=len(output.rankings),
                short=tuple(short),
                outside=tuple(outside),
            )
        )
    return UniverseCheck(
        expected=len(expected),
        corpus=len(corpus_ids),
        missing=tuple(sorted(expected - corpus_ids)),
        extra=tuple(sorted(corpus_ids - expected)),
        rankers=tuple(coverage),
    )


__all__ = ["RankerCoverage", "UniverseCheck", "check_universe", "read_entity_ids"]
