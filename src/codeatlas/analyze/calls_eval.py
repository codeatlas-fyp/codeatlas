"""Score CALLS resolution against hand-labelled call sites (plan item SG-07).

Unit tests show the resolver does what we meant; they do not show how often it is right on
real code. This module measures that on call sites that people label without seeing
CodeAtlas's answer.

Steps (the script `scripts/calls_eval.py` does the file reading and writing):

1. `find_call_sites`: every call expression in the repository, including calls the resolver
   never records (`x[0]()`, `super().m()`), so calls it skips count as omissions.
2. `stratify` + `sample_stratified`: each site gets a stratum from name bindings only
   (`calls.call_site_strata`; never from the resolver's answer), and a seeded sample takes the
   same number from each stratum: `local`, `cross_file`, `ambiguous_dynamic`, `external`.
3. `sheet_rows` writes a blind sheet: the call and its source line, never the resolver's
   answer and never the stratum (the stratum would hint at the answer); strata go to a separate
   file used only when scoring.
4. Annotators fill two columns per site:
   - `resolvability`: `resolvable` (it runs repository code), `external` (standard library,
     third-party or builtin), `dynamic_unknown` (the source text does not decide it, even for
     a person), or `skip` (not a real call site; left out of every count);
   - `expected_target_ids`: for `resolvable` only, every repository entity the call can run,
     separated by `;` (a class call means its `__init__` when it has one, as in the resolver).
   Sheets from the first version with a single `gold` column are still read.
5. `agreement` compares two annotators; disagreements are settled by a third person.
6. `score` compares the settled labels with `resolve_call_sites`.

Outcome per labelled site:
- confident edge to X: `correct` if X is an expected target, `wrong_target` if the site is
  resolvable but X is not a target, `overclaim` if the site is external or dynamic_unknown;
- unconfident edge to X: `guess_correct` if X is an expected target, else `guess_wrong`;
- no edge: `omitted` if the site is resolvable, `abstained` if external or dynamic_unknown (a
  dynamic call is never counted as a known miss).

Confident errors (`wrong_target`, `overclaim`) are reported apart from guesses and omissions,
because a wrong confident edge is shown to reviewers as fact. Every metric is a numerator and
a denominator. Edge-level counts are reported next to site-level ones, because one resolvable
site can have several expected targets.

Pure: no file I/O, no clock; the sample depends only on the sites, strata and seed.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from codeatlas.analyze.calls import STRATA, CallSite
from codeatlas.analyze.code import _parser

RESOLVABLE = "resolvable"
EXTERNAL = "external"
DYNAMIC = "dynamic_unknown"
SKIP = "skip"
RESOLVABILITY = (RESOLVABLE, EXTERNAL, DYNAMIC, SKIP)
_LEGACY = {"EXTERNAL": EXTERNAL, "UNKNOWN": DYNAMIC, "SKIP": SKIP}

SHEET_COLUMNS = (
    "site_id",
    "repo",
    "revision",
    "file",
    "line",
    "column",
    "call_text",
    "line_text",
    "resolvability",
    "expected_target_ids",
    "notes",
)
STRATA_COLUMNS = ("site_id", "stratum")
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
_STRING_LITERAL = re.compile(r"""^[rbuf]{0,2}['"]""", re.IGNORECASE)


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


@dataclass(frozen=True)
class Label:
    resolvability: str
    targets: tuple[str, ...] = ()  # sorted, only for resolvable sites

    def show(self) -> str:
        return (
            f"{self.resolvability}[{';'.join(self.targets)}]"
            if self.targets
            else (self.resolvability)
        )


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


def stratify(
    sites: Iterable[SiteRef], strata_by_position: Mapping[tuple[str, int, int], str]
) -> dict[str, str]:
    """site_id -> stratum. A method on a string literal (`"...".format()`) is `external`; other
    sites the resolver never records (`x[0]()`, `f()()`) are `ambiguous_dynamic`."""
    out: dict[str, str] = {}
    for s in sites:
        stratum = strata_by_position.get((s.file, s.line, s.column))
        if stratum is None:
            literal = _STRING_LITERAL.match(s.call_text) is not None
            stratum = "external" if literal else "ambiguous_dynamic"
        out[s.site_id] = stratum
    return out


@dataclass(frozen=True)
class Sample:
    sites: list[SiteRef]
    available: dict[str, int]  # stratum -> sites in the source
    taken: dict[str, int]  # stratum -> sites in the sample

    def shortfall(self, per_stratum: int) -> dict[str, int]:
        return {s: per_stratum - n for s, n in self.taken.items() if n < per_stratum}


def sample_stratified(
    sites: Iterable[SiteRef], strata: Mapping[str, str], per_stratum: int, seed: int
) -> Sample:
    """`per_stratum` sites from each stratum (all of them if fewer), seeded, sorted."""
    by_stratum: dict[str, list[SiteRef]] = {s: [] for s in STRATA}
    for site in sorted(sites, key=lambda s: (s.file, s.line, s.column)):
        by_stratum.setdefault(strata[site.site_id], []).append(site)
    rng = random.Random(seed)  # noqa: S311 - sampling, not security
    chosen: list[SiteRef] = []
    taken: dict[str, int] = {}
    for stratum in sorted(by_stratum, key=_stratum_order):
        pool = by_stratum[stratum]
        pick = pool if len(pool) <= per_stratum else rng.sample(pool, per_stratum)
        chosen += pick
        taken[stratum] = len(pick)
    return Sample(
        sites=sorted(chosen, key=lambda s: (s.file, s.line, s.column)),
        available={s: len(v) for s, v in by_stratum.items()},
        taken=taken,
    )


def sheet_rows(sites: Iterable[SiteRef], repo: str, revision: str) -> list[dict[str, str]]:
    """The blind annotation sheet: the call and its line; label columns left empty."""
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
            "resolvability": "",
            "expected_target_ids": "",
            "notes": "",
        }
        for s in sites
    ]


def parse_labels(rows: Iterable[Mapping[str, str]]) -> dict[str, Label]:
    """`site_id -> Label` from a filled sheet. Rejects empty, repeated or inconsistent labels."""
    labels: dict[str, Label] = {}
    for row in rows:
        sid = (row.get("site_id") or "").strip()
        if not sid:
            raise ValueError("a row has no site_id")
        if sid in labels:
            raise ValueError(f"{sid} is labelled twice")
        labels[sid] = _label(sid, row)
    return labels


def _label(sid: str, row: Mapping[str, str]) -> Label:
    kind = (row.get("resolvability") or "").strip()
    raw_targets = (row.get("expected_target_ids") or "").strip()
    gold = (row.get("gold") or "").strip()
    if not kind and gold:  # first-version sheet
        if gold in _LEGACY:
            return Label(_LEGACY[gold])
        return Label(RESOLVABLE, (gold,))
    if kind not in RESOLVABILITY:
        raise ValueError(f"{sid}: resolvability must be one of {', '.join(RESOLVABILITY)}")
    targets = tuple(sorted({t.strip() for t in raw_targets.split(";") if t.strip()}))
    if kind == RESOLVABLE and not targets:
        raise ValueError(f"{sid}: a resolvable site needs expected_target_ids")
    if kind != RESOLVABLE and targets:
        raise ValueError(f"{sid}: only a resolvable site has expected_target_ids")
    return Label(kind, targets)


@dataclass(frozen=True)
class Agreement:
    compared: int
    agreed: int
    disagreements: list[tuple[str, str, str]]  # (site_id, first, second)
    only_in_one: list[str]

    @property
    def rate(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


def agreement(first: Mapping[str, Label], second: Mapping[str, Label]) -> Agreement:
    """Raw agreement on resolvability and the full target set, on sites both labelled."""
    shared = sorted(set(first) & set(second))
    disagreements = [
        (s, first[s].show(), second[s].show()) for s in shared if first[s] != second[s]
    ]
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
    label: Label
    callee: str | None
    confident: bool
    outcome: str
    stratum: str | None = None


@dataclass(frozen=True)
class Report:
    labelled: int  # sites with a label other than skip
    skipped: int
    unmatched: list[str]  # labelled site ids that are not call sites in this source
    counts: dict[str, int] = field(default_factory=dict)
    outcomes: list[SiteOutcome] = field(default_factory=list)

    @property
    def resolvable_sites(self) -> int:
        return sum(1 for o in self.outcomes if o.label.resolvability == RESOLVABLE)

    def metrics(self) -> dict[str, tuple[int, int]]:
        """Site-level: name -> (numerator, denominator); the caller divides."""
        c = self.counts
        confident = c["correct"] + c["wrong_target"] + c["overclaim"]
        confident_errors = c["wrong_target"] + c["overclaim"]
        guesses = c["guess_correct"] + c["guess_wrong"]
        resolvable = self.resolvable_sites
        other = self.labelled - resolvable
        missed = resolvable - c["correct"]
        return {
            "confident precision": (c["correct"], confident),
            "confident recall": (c["correct"], resolvable),
            "confident F1": (2 * c["correct"], 2 * c["correct"] + confident_errors + missed),
            "false-confident rate": (confident_errors, self.labelled),
            "unconfident precision": (c["guess_correct"], guesses),
            "recall, confident or not": (c["correct"] + c["guess_correct"], resolvable),
            "omission rate": (c["omitted"], resolvable),
            "unresolved rate (no edge)": (c["omitted"] + c["abstained"], self.labelled),
            "correct abstention on external/dynamic": (c["abstained"], other),
        }

    def edge_counts(self) -> dict[str, dict[str, int]]:
        """Edge-level TP / FP / FN, for confident edges and for all edges."""
        out = {}
        for name, allow_guess in (("confident edges", False), ("all edges", True)):
            tp = fp = fn = 0
            for o in self.outcomes:
                predicted = o.callee is not None and (o.confident or allow_guess)
                hit = predicted and o.callee in o.label.targets
                tp += int(hit)
                fp += int(predicted and not hit)
                fn += len(o.label.targets) - int(hit)
            out[name] = {"TP": tp, "FP": fp, "FN": fn}
        return out

    def by_stratum(self) -> dict[str, dict[str, int]]:
        table: dict[str, dict[str, int]] = {}
        for o in self.outcomes:
            if o.stratum is None:
                continue
            row = table.setdefault(o.stratum, dict.fromkeys(OUTCOMES, 0))
            row[o.outcome] += 1
        return dict(sorted(table.items(), key=lambda kv: _stratum_order(kv[0])))


def score(
    predictions: Mapping[str, CallSite],
    labels: Mapping[str, Label],
    call_sites: Iterable[str],
    strata: Mapping[str, str] | None = None,
) -> Report:
    """Compare the resolver's answer at each labelled site with its label.

    `call_sites` are the ids `find_call_sites` gives for the same source; a label for any other
    id (a sheet from another revision) is left out and listed in `unmatched`.
    """
    known = set(call_sites)
    counts = dict.fromkeys(OUTCOMES, 0)
    outcomes: list[SiteOutcome] = []
    skipped = 0
    unmatched: list[str] = []
    for sid in sorted(labels):
        label = labels[sid]
        if sid not in known:
            unmatched.append(sid)
            continue
        if label.resolvability == SKIP:
            skipped += 1
            continue
        site = predictions.get(sid)
        callee = site.callee if site is not None else None
        confident = bool(site and site.callee is not None and site.confident)
        outcome = _outcome(label, callee, confident)
        counts[outcome] += 1
        stratum = strata.get(sid) if strata is not None else None
        outcomes.append(SiteOutcome(sid, label, callee, confident, outcome, stratum))
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
    lines.append(
        f"- Labelled call sites scored: {report.labelled} (skip: {report.skipped}; "
        f"resolvable: {report.resolvable_sites})"
    )
    if report.unmatched:
        lines.append(
            f"- Labelled sites not found in this source (left out): {len(report.unmatched)}"
        )
    lines += ["", "## Site level", ""]
    lines += ["| Metric | Value | Numerator / denominator |", "| --- | --- | --- |"]
    for name, (num, den) in report.metrics().items():
        value = f"{num / den:.3f}" if den else "n/a"
        lines.append(f"| {name} | {value} | {num} / {den} |")
    lines += ["", "| Outcome | Sites |", "| --- | --- |"]
    lines += [f"| `{name}` | {report.counts[name]} |" for name in OUTCOMES]
    lines += ["", "## Edge level", "", "| Edges | TP | FP | FN |", "| --- | --- | --- | --- |"]
    for name, c in report.edge_counts().items():
        lines.append(f"| {name} | {c['TP']} | {c['FP']} | {c['FN']} |")
    strata = report.by_stratum()
    if strata:
        lines += ["", "## By stratum", ""]
        lines += ["| Stratum | " + " | ".join(OUTCOMES) + " |", "|---" * (len(OUTCOMES) + 1) + "|"]
        for name, row in strata.items():
            lines.append(f"| {name} | " + " | ".join(str(row[o]) for o in OUTCOMES) + " |")
    errors = [o for o in report.outcomes if o.outcome in ("wrong_target", "overclaim")]
    lines += ["", "## Confident errors", ""]
    if errors:
        lines += ["| Site | Label | Resolver (confident) |", "| --- | --- | --- |"]
        lines += [f"| `{o.site_id}` | `{o.label.show()}` | `{o.callee}` |" for o in errors]
    else:
        lines.append("None.")
    rest = [o for o in report.outcomes if o.outcome in ("guess_wrong", "omitted")]
    lines += ["", "## Wrong guesses and omissions", ""]
    if rest:
        lines += ["| Site | Outcome | Label | Resolver |", "| --- | --- | --- | --- |"]
        lines += [
            f"| `{o.site_id}` | `{o.outcome}` | `{o.label.show()}` | `{o.callee or '-'}` |"
            for o in rest
        ]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _outcome(label: Label, callee: str | None, confident: bool) -> str:
    resolvable = label.resolvability == RESOLVABLE
    if callee is None:
        return "omitted" if resolvable else "abstained"
    hit = callee in label.targets
    if confident:
        if hit:
            return "correct"
        return "wrong_target" if resolvable else "overclaim"
    return "guess_correct" if hit else "guess_wrong"


def _stratum_order(name: str) -> tuple[int, str]:
    return (STRATA.index(name), name) if name in STRATA else (len(STRATA), name)


def _strength(site: CallSite) -> int:
    if site.callee is None:
        return 0
    return 2 if site.confident else 1


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 3] + "..."
