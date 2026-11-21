"""CALLS evaluation (SG-07): strata, blind sampling, labels, agreement and scoring.

The source and labels here are synthetic and only check the arithmetic; they are not an
accuracy result.
"""

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from codeatlas.analyze import calls_eval
from codeatlas.analyze.calls import (
    STRATA,
    CallSite,
    build_call_graph,
    call_site_strata,
    resolve_call_sites,
)
from codeatlas.analyze.calls_eval import Label
from codeatlas.analyze.code import extract_entities
from codeatlas.schema import CodeEntity

SCRIPT = Path(__file__).parents[2] / "scripts" / "calls_eval.py"

SOURCES = {
    "pkg/__init__.py": b"",
    "pkg/fees.py": (
        b"import json\n"
        b"\n"
        b"def charge(x):\n"
        b"    return json.dumps(x)\n"
        b"\n"
        b"class Ledger:\n"
        b"    def save(self):\n"
        b"        return charge(1)\n"
    ),
    "pkg/loans.py": (
        b"from pkg.fees import charge, Ledger\n"
        b"\n"
        b"def close(fn, ledger):\n"
        b"    charge(2)\n"
        b"    fn()\n"
        b"    ledger.save()\n"
        b"    handlers[0]()\n"
        b"    print('done')\n"
        b"    return Ledger()\n"
    ),
}


def _entities() -> list[CodeEntity]:
    return [e for p, s in SOURCES.items() for e in extract_entities(file_path=p, source=s)]


def _strata() -> dict[str, str]:
    sites = calls_eval.find_call_sites(SOURCES)
    by_position = call_site_strata(entities=_entities(), file_sources=SOURCES)
    return calls_eval.stratify(sites, by_position)


def load_script() -> ModuleType:
    # scripts/ is not a package, so load the file directly.
    spec = importlib.util.spec_from_file_location("calls_eval_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_call_expression_is_a_site_even_ones_the_resolver_skips() -> None:
    sites = calls_eval.find_call_sites(SOURCES)
    assert [(s.file, s.line, s.call_text) for s in sites] == [
        ("pkg/fees.py", 4, "json.dumps"),
        ("pkg/fees.py", 8, "charge"),
        ("pkg/loans.py", 4, "charge"),
        ("pkg/loans.py", 5, "fn"),
        ("pkg/loans.py", 6, "ledger.save"),
        ("pkg/loans.py", 7, "handlers[0]"),
        ("pkg/loans.py", 8, "print"),
        ("pkg/loans.py", 9, "Ledger"),
    ]
    assert sites[0].site_id == "pkg/fees.py:4:12"


def test_strata_come_from_bindings_only() -> None:
    assert _strata() == {
        "pkg/fees.py:4:12": "external",  # json is not a repository module
        "pkg/fees.py:8:16": "local",  # charge is defined in the same module
        "pkg/loans.py:4:5": "cross_file",  # imported from pkg.fees
        "pkg/loans.py:5:5": "ambiguous_dynamic",  # a parameter
        "pkg/loans.py:6:5": "ambiguous_dynamic",  # a method on a parameter
        "pkg/loans.py:7:5": "ambiguous_dynamic",  # never recorded by the resolver
        "pkg/loans.py:8:5": "external",  # a builtin
        "pkg/loans.py:9:12": "cross_file",  # imported class
    }
    assert set(_strata().values()) <= set(STRATA)


def test_super_and_attribute_chains_on_self_are_ambiguous() -> None:
    sources = {
        "pkg/__init__.py": b"",
        "pkg/a.py": (
            b"class A:\n"
            b"    def run(self):\n"
            b"        self.log.info('x')\n"
            b"        super().run()\n"
            b"        super(A, self).run()\n"
            b"        self.run()\n"
            b"        '{}'.format(1)\n"
        ),
    }
    entities = [e for p, s in sources.items() for e in extract_entities(file_path=p, source=s)]
    sites = calls_eval.find_call_sites(sources)
    by_text = calls_eval.stratify(sites, call_site_strata(entities=entities, file_sources=sources))
    assert {s.call_text: by_text[s.site_id] for s in sites} == {
        "self.log.info": "ambiguous_dynamic",
        "super().run": "ambiguous_dynamic",
        "super": "ambiguous_dynamic",
        "super(A, self).run": "ambiguous_dynamic",
        "self.run": "local",
        "'{}'.format": "external",  # a method on a string literal
    }


def test_resolver_sites_line_up_with_found_sites() -> None:
    found = {s.site_id for s in calls_eval.find_call_sites(SOURCES)}
    resolved = calls_eval.predictions_by_site(
        resolve_call_sites(entities=_entities(), file_sources=SOURCES)
    )
    assert set(resolved) <= found
    assert "pkg/loans.py:7:5" not in resolved
    assert resolved["pkg/loans.py:4:5"].callee == "pkg.fees.charge"
    assert resolved["pkg/loans.py:6:5"].callee == "pkg.fees.Ledger.save"
    assert resolved["pkg/loans.py:6:5"].confident is False


def test_call_sites_give_the_same_edges_as_the_call_graph() -> None:
    edges = build_call_graph(entities=_entities(), file_sources=SOURCES)
    sites = resolve_call_sites(entities=_entities(), file_sources=SOURCES)
    assert {(e.src, e.dst) for e in edges} == {
        (s.caller, s.callee) for s in sites if s.callee is not None
    }


def test_stratified_sample_is_seeded_balanced_and_reports_shortfall() -> None:
    sites = calls_eval.find_call_sites(SOURCES)
    strata = _strata()
    sample = calls_eval.sample_stratified(sites, strata, per_stratum=2, seed=7)
    assert sample == calls_eval.sample_stratified(list(reversed(sites)), strata, 2, 7)
    assert sample.taken == {"local": 1, "cross_file": 2, "ambiguous_dynamic": 2, "external": 2}
    assert sample.available == {"local": 1, "cross_file": 2, "ambiguous_dynamic": 3, "external": 2}
    assert sample.shortfall(2) == {"local": 1}
    assert sample.sites == sorted(sample.sites, key=lambda s: (s.file, s.line, s.column))


def test_sheet_is_blind() -> None:
    rows = calls_eval.sheet_rows(calls_eval.find_call_sites(SOURCES), "o/r", "abc")
    assert tuple(rows[0]) == calls_eval.SHEET_COLUMNS
    assert all(r["resolvability"] == "" and r["expected_target_ids"] == "" for r in rows)
    assert not any(k in rows[0] for k in ("callee", "confident", "stratum"))


def test_labels_are_validated_and_old_sheets_still_read() -> None:
    labels = calls_eval.parse_labels(
        [
            {"site_id": "a:1:1", "resolvability": "resolvable", "expected_target_ids": "m.g; m.f"},
            {"site_id": "a:2:1", "resolvability": "external", "expected_target_ids": ""},
            {"site_id": "a:3:1", "gold": "UNKNOWN"},
            {"site_id": "a:4:1", "gold": "m.h"},
        ]
    )
    assert labels == {
        "a:1:1": Label("resolvable", ("m.f", "m.g")),
        "a:2:1": Label("external"),
        "a:3:1": Label("dynamic_unknown"),
        "a:4:1": Label("resolvable", ("m.h",)),
    }
    bad = [
        ({"site_id": "a:1:1", "resolvability": "maybe"}, "must be one of"),
        ({"site_id": "a:1:1", "resolvability": "resolvable"}, "needs expected_target_ids"),
        (
            {"site_id": "a:1:1", "resolvability": "external", "expected_target_ids": "m.f"},
            "only a resolvable",
        ),
    ]
    for row, message in bad:
        with pytest.raises(ValueError, match=message):
            calls_eval.parse_labels([row])
    with pytest.raises(ValueError, match="twice"):
        calls_eval.parse_labels([{"site_id": "a:1:1", "resolvability": "skip"}] * 2)


def test_agreement_compares_the_whole_target_set() -> None:
    a = {"x": Label("resolvable", ("m.f",)), "y": Label("external"), "z": Label("skip")}
    b = {"x": Label("resolvable", ("m.f", "m.g")), "y": Label("external")}
    result = calls_eval.agreement(a, b)
    assert (result.compared, result.agreed, result.rate) == (2, 1, 0.5)
    assert result.disagreements == [("x", "resolvable[m.f]", "resolvable[m.f;m.g]")]
    assert result.only_in_one == ["z"]


def _site(sid: str, callee: str | None, confident: bool) -> CallSite:
    file, line, column = sid.split(":")
    return CallSite(file, int(line), int(column), "caller", "f", callee, confident)


def test_outcomes_metrics_edges_and_strata() -> None:
    predictions = {
        "f:1:1": _site("f:1:1", "a.x", True),  # correct (one of two targets)
        "f:2:1": _site("f:2:1", "a.x", True),  # wrong_target
        "f:3:1": _site("f:3:1", "a.x", True),  # overclaim (external)
        "f:4:1": _site("f:4:1", "a.y", False),  # guess_correct
        "f:5:1": _site("f:5:1", "a.y", False),  # guess_wrong (dynamic_unknown)
        # f:6:1 no prediction, resolvable -> omitted
        # f:7:1 no prediction, dynamic_unknown -> abstained (not a known miss)
    }
    labels = {
        "f:1:1": Label("resolvable", ("a.x", "a.z")),
        "f:2:1": Label("resolvable", ("a.y",)),
        "f:3:1": Label("external"),
        "f:4:1": Label("resolvable", ("a.y",)),
        "f:5:1": Label("dynamic_unknown"),
        "f:6:1": Label("resolvable", ("a.z",)),
        "f:7:1": Label("dynamic_unknown"),
        "f:8:1": Label("skip"),
        "other:1:1": Label("resolvable", ("a.x",)),  # not a call site in this source
    }
    strata = {f"f:{i}:1": ("local" if i < 4 else "ambiguous_dynamic") for i in range(1, 9)}
    report = calls_eval.score(predictions, labels, [f"f:{i}:1" for i in range(1, 9)], strata)
    assert report.counts == {
        "correct": 1,
        "wrong_target": 1,
        "overclaim": 1,
        "guess_correct": 1,
        "guess_wrong": 1,
        "omitted": 1,
        "abstained": 1,
    }
    assert (report.labelled, report.skipped, report.unmatched) == (7, 1, ["other:1:1"])
    assert report.resolvable_sites == 4
    assert report.metrics() == {
        "confident precision": (1, 3),
        "confident recall": (1, 4),
        "confident F1": (2, 7),  # 2TP / (2TP + 2 confident errors + 3 resolvable misses)
        "false-confident rate": (2, 7),
        "unconfident precision": (1, 2),
        "recall, confident or not": (2, 4),
        "omission rate": (1, 4),
        "unresolved rate (no edge)": (2, 7),
        "correct abstention on external/dynamic": (1, 3),
    }
    # Targets: f1 has 2 (a.x hit, a.z missed), f2 1, f4 1, f6 1 -> 5 expected edges.
    assert report.edge_counts() == {
        "confident edges": {"TP": 1, "FP": 2, "FN": 4},
        "all edges": {"TP": 2, "FP": 3, "FN": 3},
    }
    assert report.by_stratum() == {
        "local": {
            **dict.fromkeys(calls_eval.OUTCOMES, 0),
            "correct": 1,
            "wrong_target": 1,
            "overclaim": 1,
        },
        "ambiguous_dynamic": {
            **dict.fromkeys(calls_eval.OUTCOMES, 0),
            "guess_correct": 1,
            "guess_wrong": 1,
            "omitted": 1,
            "abstained": 1,
        },
    }
    text = calls_eval.render_report(report, "test")
    assert "| confident precision | 0.333 | 1 / 3 |" in text
    assert "| confident edges | 1 | 2 | 4 |" in text
    assert "| local | 1 | 1 | 1 | 0 | 0 | 0 | 0 |" in text
    assert "| `f:2:1` | `resolvable[a.y]` | `a.x` |" in text


def test_empty_denominators_show_n_a() -> None:
    report = calls_eval.score({}, {}, [])
    assert "| confident precision | n/a | 0 / 0 |" in calls_eval.render_report(report, "empty")


def test_script_samples_by_stratum_then_scores(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for path, data in SOURCES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    script = load_script()
    sheet = tmp_path / "sheet.csv"
    args = ["--root", str(root), "--include", "pkg"]
    sample_args = ["--repo", "o/r", "--revision", "abc", "--per-stratum", "5"]
    assert script.main(["sample", *args, *sample_args, "--out", str(sheet)]) == 0
    strata_file = tmp_path / "sheet_strata.csv"
    with sheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with strata_file.open(encoding="utf-8", newline="") as handle:
        strata_rows = list(csv.DictReader(handle))
    assert len(rows) == len(strata_rows) == 8
    labels = {
        "json.dumps": ("external", ""),
        "charge": ("resolvable", "pkg.fees.charge"),
        "fn": ("dynamic_unknown", ""),
        "ledger.save": ("resolvable", "pkg.fees.Ledger.save"),
        "handlers[0]": ("dynamic_unknown", ""),
        "print": ("external", ""),
        "Ledger": ("resolvable", "pkg.fees.Ledger"),
    }
    for row in rows:
        row["resolvability"], row["expected_target_ids"] = labels[row["call_text"]]
    labelled = tmp_path / "labels.csv"
    script.write_csv(labelled, calls_eval.SHEET_COLUMNS, rows)
    report = tmp_path / "report.md"
    score_args = ["--labels", str(labelled), "--strata", str(strata_file), "--out", str(report)]
    assert script.main(["score", *args, *score_args]) == 0
    text = report.read_text(encoding="utf-8")
    # charge x2 and Ledger() are confident and right; ledger.save is a right guess;
    # json.dumps, fn, handlers[0] and print get no edge, which is right for each.
    assert "| confident precision | 1.000 | 3 / 3 |" in text
    assert "| recall, confident or not | 1.000 | 4 / 4 |" in text
    assert "| correct abstention on external/dynamic | 1.000 | 4 / 4 |" in text
    assert "## By stratum" in text
