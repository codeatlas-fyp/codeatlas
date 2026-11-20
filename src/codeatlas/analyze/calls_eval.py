"""Score CALLS resolution against hand-labelled call sites (plan item SG-07).

Unit tests show the resolver does what we meant; they do not show how often it is right on
real code. This module measures that on a sample of call sites that people label without
seeing CodeAtlas's answer.

Steps (the script `scripts/calls_eval.py` does the file reading and writing):

1. `find_call_sites`: every call expression in the repository, including calls the resolver
   never records (`x[0]()`, `super().m()`), so calls it skips count as omissions.
2. `sample_sites`: a seeded random sample, written to a blind sheet (`sheet_rows`) that holds
   the call and its source line, never the resolver's answer.
3. Annotators fill `gold` for each site:
   - the repository entity the call runs (`pkg.mod.Class.method`; a class call means its
     `__init__` when it has one, as in the resolver);
   - `EXTERNAL`: it runs code outside the repository (standard library, third-party, builtin);
   - `UNKNOWN`: the source text does not decide it (dynamic dispatch, several possible targets);
   - `SKIP`: not a real call site (left out of every count).
4. `agreement` compares two annotators' sheets; disagreements are settled by a third person.
5. `score` compares the settled labels with `resolve_call_sites`.

Each labelled site gets one outcome:

- confident edge to X: `correct` if gold is X, `wrong_target` if gold is another entity,
  `overclaim` if gold is EXTERNAL or UNKNOWN;
- unconfident edge to X: `guess_correct` if gold is X, else `guess_wrong`;
- no edge: `omitted` if gold is an entity, `abstained` if gold is EXTERNAL or UNKNOWN.

Confident errors (`wrong_target`, `overclaim`) are reported apart from guesses and omissions,
because a wrong confident edge is shown to reviewers as fact.

Pure: no file I/O, no clock; the sample depends only on the sites and the seed.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from codeatlas.analyze.calls import CallSite
from codeatlas.analyze.code import _parser

EXTERNAL = "EXTERNAL"
UNKNOWN = "UNKNOWN"
SKIP = "SKIP"
NON_ENTITY = frozenset({EXTERNAL, UNKNOWN})

SHEET_COLUMNS = (
    "site_id",
    "repo",
    "revision",
    "file",
    "line",
    "column",
    "call_text",
    "line_text",
    "gold",
    "notes",
)
OUTCOMES = (
    "correct",
    "wrong_target",
    "overclaim",
    "guess_correct",
    "guess_wrong",
    "omitted",
    "abstained",
)
_MAX_TEXT = 120


@dataclass(frozen=True)
class SiteRef:
    """One call expression in the source, as an annotator sees it."""

    file: str
    line: int  # 1-based
    column: int  # 1-based, in bytes, as tree-sitter and `resolve_call_sites` count
    call_text: str  # the callee as written, e.g. `self.repo.save`
    line_text: str  # the source line, stripped

    @property
    def site_id(self) -> str:
        return site_id(self.file, self.line, self.column)


def site_id(file: str, line: int, column: int) -> str:
    return f"{file}:{line}:{column}"


def find_call_sites(file_sources: Mapping[str, bytes]) -> list[SiteRef]:
    """Every call expression in the Python files, sorted by file, line and column."""
    try:
        parser = _parser()
    except ImportError:
        return []
    sites: list[SiteRef] = []
    for path in sorted(file_sources):
        if not path.endswith(".py"):
            continue
        source = file_sources[path]
        tree = parser.parse(source)
        if tree.root_node is None:
            continue
        lines = source.decode("utf-8", errors="replace").splitlines()
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type == "call":
                fn = node.child_by_field_name("function")
                text = fn.text if fn is not None and fn.text is not None else b""
                row, column = node.start_point
                sites.append(
                    SiteRef(
                        file=path,
                        line=row + 1,
                        column=column + 1,
                        call_text=_short(text.decode("utf-8", errors="replace")),
                        line_text=_short(lines[row].strip() if row < len(lines) else ""),
                    )
                )
            stack.extend(node.children)
    return sorted(sites, key=lambda s: (s.file, s.line, s.column))


def sample_sites(sites: Iterable[SiteRef], n: int, seed: int) -> list[SiteRef]:
    """`n` sites chosen at random with `seed` (all of them if there are fewer), sorted."""
    ordered = sorted(sites, key=lambda s: (s.file, s.line, s.column))
    if n >= len(ordered):
        return ordered
    chosen = random.Random(seed).sample(ordered, n)  # noqa: S311 - sampling, not security
    return sorted(chosen, key=lambda s: (s.file, s.line, s.column))


def sheet_rows(sites: Iterable[SiteRef], repo: str, revision: str) -> list[dict[str, str]]:
    """The blind annotation sheet: the call and its line, `gold` and `notes` left empty."""
    return [
        {
            "site_id": s.site_id,
            "repo": repo,
            "revision": revision,
            "file": s.file,
            "line": str(s.line),
            "column": str(s.column),
            "call_text": s.call_text,
            "line_text": s.line_text,
            "gold": "",
            "notes": "",
        }
        for s in sites
    ]


def parse_labels(rows: Iterable[Mapping[str, str]]) -> dict[str, str]:
    """`site_id -> gold` from a filled sheet; an empty or repeated label is an error."""
    labels: dict[str, str] = {}
    for row in rows:
        sid = (row.get("site_id") or "").strip()
        gold = (row.get("gold") or "").strip()
        if not sid:
            raise ValueError("a row has no site_id")
        if not gold:
            raise ValueError(f"{sid} has no gold label")
        if sid in labels:
            raise ValueError(f"{sid} is labelled twice")
        labels[sid] = gold
    return labels


@dataclass(frozen=True)
class Agreement:
    compared: int
    agreed: int
    disagreements: list[tuple[str, str, str]]  # (site_id, first, second)
    only_in_one: list[str]

    @property
    def rate(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


def agreement(first: Mapping[str, str], second: Mapping[str, str]) -> Agreement:
    """Raw agreement between two annotators on the sites both labelled."""
    shared = sorted(set(first) & set(second))
    disagreements = [(s, first[s], second[s]) for s in shared if first[s] != second[s]]
    return Agreement(
        compared=len(shared),
        agreed=len(shared) - len(disagreements),
        disagreements=disagreements,
        only_in_one=sorted(set(first) ^ set(second)),
    )


def predictions_by_site(sites: Iterable[CallSite]) -> dict[str, CallSite]:
    """One resolver answer per position; if a position was recorded twice, the firmer wins."""
    best: dict[str, CallSite] = {}
    for site in sites:
        key = site_id(site.file, site.line, site.column)
        old = best.get(key)
        if old is None or _strength(site) > _strength(old):
            best[key] = site
    return best


@dataclass(frozen=True)
class SiteOutcome:
    site_id: str
    gold: str
    callee: str | None
    confident: bool
    outcome: str


@dataclass(frozen=True)
class Report:
    labelled: int  # sites with a gold label other than SKIP
    skipped: int
    unmatched: list[str]  # labelled site ids that are not call sites in this source
    counts: dict[str, int] = field(default_factory=dict)
    outcomes: list[SiteOutcome] = field(default_factory=list)

    @property
    def gold_entity_sites(self) -> int:
        return sum(1 for o in self.outcomes if o.gold not in NON_ENTITY)

    def metrics(self) -> dict[str, tuple[int, int]]:
        """name -> (numerator, denominator); the caller divides, so both are always shown."""
        c = self.counts
        confident = c["correct"] + c["wrong_target"] + c["overclaim"]
        guesses = c["guess_correct"] + c["guess_wrong"]
        entity = self.gold_entity_sites
        non_entity = self.labelled - entity
        return {
            "confident precision": (c["correct"], confident),
            "unconfident precision": (c["guess_correct"], guesses),
            "confident recall": (c["correct"], entity),
            "recall, confident or not": (c["correct"] + c["guess_correct"], entity),
            "omission rate": (c["omitted"], entity),
            "correct abstention on EXTERNAL/UNKNOWN": (c["abstained"], non_entity),
        }


def score(
    predictions: Mapping[str, CallSite],
    labels: Mapping[str, str],
    call_sites: Iterable[str],
) -> Report:
    """Compare the resolver's answer at each labelled site with its gold label.

    `call_sites` are the ids `find_call_sites` gives for the same source; a label for any other
    id (a sheet from another revision) is left out and listed in `unmatched`.
    """
    known = set(call_sites)
    counts = dict.fromkeys(OUTCOMES, 0)
    outcomes: list[SiteOutcome] = []
    skipped = 0
    unmatched: list[str] = []
    for sid in sorted(labels):
        gold = labels[sid]
        if sid not in known:
            unmatched.append(sid)
            continue
        if gold == SKIP:
            skipped += 1
            continue
        site = predictions.get(sid)
        callee = site.callee if site is not None else None
        confident = bool(site and site.callee is not None and site.confident)
        outcome = _outcome(gold, callee, confident)
        counts[outcome] += 1
        outcomes.append(SiteOutcome(sid, gold, callee, confident, outcome))
    return Report(
        labelled=len(outcomes),
        skipped=skipped,
        unmatched=unmatched,
        counts=counts,
        outcomes=outcomes,
    )


def render_report(report: Report, title: str, details: Iterable[str] = ()) -> str:
    """Markdown with every value next to its numerator and denominator."""
    lines = [f"# {title}", ""]
    lines += [f"- {d}" for d in details]
    lines += [
        f"- Labelled call sites scored: {report.labelled} (SKIP: {report.skipped}; "
        f"gold is a repository entity: {report.gold_entity_sites})",
    ]
    if report.unmatched:
        lines.append(
            f"- Labelled sites not found in this source (left out): {len(report.unmatched)}"
        )
    lines += ["", "| Metric | Value | Numerator / denominator |", "| --- | --- | --- |"]
    for name, (num, den) in report.metrics().items():
        value = f"{num / den:.3f}" if den else "n/a"
        lines.append(f"| {name} | {value} | {num} / {den} |")
    lines += ["", "| Outcome | Sites |", "| --- | --- |"]
    lines += [f"| `{name}` | {report.counts[name]} |" for name in OUTCOMES]
    errors = [o for o in report.outcomes if o.outcome in ("wrong_target", "overclaim")]
    lines += ["", "## Confident errors", ""]
    if errors:
        lines += ["| Site | Gold | Resolver (confident) |", "| --- | --- | --- |"]
        lines += [f"| `{o.site_id}` | `{o.gold}` | `{o.callee}` |" for o in errors]
    else:
        lines.append("None.")
    rest = [o for o in report.outcomes if o.outcome in ("guess_wrong", "omitted")]
    lines += ["", "## Wrong guesses and omissions", ""]
    if rest:
        lines += ["| Site | Outcome | Gold | Resolver |", "| --- | --- | --- | --- |"]
        lines += [
            f"| `{o.site_id}` | `{o.outcome}` | `{o.gold}` | `{o.callee or '-'}` |" for o in rest
        ]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _outcome(gold: str, callee: str | None, confident: bool) -> str:
    if callee is None:
        return "abstained" if gold in NON_ENTITY else "omitted"
    if confident:
        if callee == gold:
            return "correct"
        return "overclaim" if gold in NON_ENTITY else "wrong_target"
    return "guess_correct" if callee == gold else "guess_wrong"


def _strength(site: CallSite) -> int:
    if site.callee is None:
        return 0
    return 2 if site.confident else 1


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."
