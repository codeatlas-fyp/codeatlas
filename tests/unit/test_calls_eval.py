"""CALLS evaluation (SG-07): blind sampling, agreement and scoring on labelled call sites.

The source and labels here are synthetic and only check the arithmetic; they are not an
accuracy result.
"""

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from codeatlas.analyze import calls_eval
from codeatlas.analyze.calls import CallSite, build_call_graph, resolve_call_sites
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


def _by_text() -> dict[str, calls_eval.SiteRef]:
    return {s.call_text: s for s in calls_eval.find_call_sites(SOURCES)}


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
    assert sites[5].line_text == "handlers[0]()"


def test_resolver_sites_line_up_with_found_sites() -> None:
    found = {s.site_id for s in calls_eval.find_call_sites(SOURCES)}
    resolved = calls_eval.predictions_by_site(
        resolve_call_sites(entities=_entities(), file_sources=SOURCES)
    )
    assert set(resolved) <= found  # the subscript call is never recorded
    assert "pkg/loans.py:7:5" not in resolved
    assert resolved["pkg/loans.py:4:5"].callee == "pkg.fees.charge"
    assert resolved["pkg/loans.py:4:5"].confident is True
    assert resolved["pkg/loans.py:6:5"].callee == "pkg.fees.Ledger.save"
    assert resolved["pkg/loans.py:6:5"].confident is False


def test_call_sites_give_the_same_edges_as_the_call_graph() -> None:
    edges = build_call_graph(entities=_entities(), file_sources=SOURCES)
    sites = resolve_call_sites(entities=_entities(), file_sources=SOURCES)
    from_sites = {(s.caller, s.callee) for s in sites if s.callee is not None}
    assert {(e.src, e.dst) for e in edges} == from_sites


def test_sample_is_seeded_sorted_and_blind() -> None:
    sites = calls_eval.find_call_sites(SOURCES)
    first = calls_eval.sample_sites(sites, 4, seed=7)
    assert first == calls_eval.sample_sites(reversed(sites), 4, seed=7)
    assert first == sorted(first, key=lambda s: (s.file, s.line, s.column))
    assert len(first) == 4
    assert calls_eval.sample_sites(sites, 100, seed=7) == sites
    rows = calls_eval.sheet_rows(first, "owner/repo", "abc123")
    assert tuple(rows[0]) == calls_eval.SHEET_COLUMNS
    assert all(r["gold"] == "" for r in rows)
    assert not any("callee" in k or "confident" in k for k in rows[0])


def test_labels_must_be_filled_once() -> None:
    assert calls_eval.parse_labels([{"site_id": "a:1:1", "gold": " EXTERNAL "}]) == {
        "a:1:1": "EXTERNAL"
    }
    with pytest.raises(ValueError, match="no gold"):
        calls_eval.parse_labels([{"site_id": "a:1:1", "gold": ""}])
    with pytest.raises(ValueError, match="twice"):
        calls_eval.parse_labels([{"site_id": "a:1:1", "gold": "x"}] * 2)


def test_agreement_lists_disagreements() -> None:
    result = calls_eval.agreement({"a": "x", "b": "y", "c": "z"}, {"a": "x", "b": "EXTERNAL"})
    assert (result.compared, result.agreed, result.rate) == (2, 1, 0.5)
    assert result.disagreements == [("b", "y", "EXTERNAL")]
    assert result.only_in_one == ["c"]


def _site(sid: str, callee: str | None, confident: bool) -> CallSite:
    file, line, column = sid.split(":")
    return CallSite(file, int(line), int(column), "caller", "f", callee, confident)


def test_each_outcome_is_counted_and_confident_errors_are_kept_apart() -> None:
    predictions = {
        "f:1:1": _site("f:1:1", "a.x", True),  # correct
        "f:2:1": _site("f:2:1", "a.x", True),  # wrong_target (gold a.y)
        "f:3:1": _site("f:3:1", "a.x", True),  # overclaim (gold EXTERNAL)
        "f:4:1": _site("f:4:1", "a.y", False),  # guess_correct
        "f:5:1": _site("f:5:1", "a.y", False),  # guess_wrong (gold UNKNOWN)
        # f:6:1 no prediction, gold a.z -> omitted
        # f:7:1 no prediction, gold EXTERNAL -> abstained
    }
    labels = {
        "f:1:1": "a.x",
        "f:2:1": "a.y",
        "f:3:1": "EXTERNAL",
        "f:4:1": "a.y",
        "f:5:1": "UNKNOWN",
        "f:6:1": "a.z",
        "f:7:1": "EXTERNAL",
        "f:8:1": "SKIP",
        "other:1:1": "a.x",  # not a call site in this source
    }
    sites = [f"f:{i}:1" for i in range(1, 9)]
    report = calls_eval.score(predictions, labels, sites)
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
    assert report.gold_entity_sites == 4
    assert report.metrics() == {
        "confident precision": (1, 3),
        "unconfident precision": (1, 2),
        "confident recall": (1, 4),
        "recall, confident or not": (2, 4),
        "omission rate": (1, 4),
        "correct abstention on EXTERNAL/UNKNOWN": (1, 3),
    }
    text = calls_eval.render_report(report, "test")
    assert "| confident precision | 0.333 | 1 / 3 |" in text
    assert "| `f:2:1` | `a.y` | `a.x` |" in text  # confident errors are listed
    assert "| `f:6:1` | `omitted` | `a.z` | `-` |" in text


def test_empty_denominators_show_n_a() -> None:
    report = calls_eval.score({}, {}, [])
    assert "| confident precision | n/a | 0 / 0 |" in calls_eval.render_report(report, "empty")


def test_script_samples_then_scores_a_labelled_sheet(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    for path, data in SOURCES.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    script = load_script()
    sheet = tmp_path / "sheet.csv"
    args = ["--root", str(root), "--include", "pkg"]
    assert (
        script.main(
            ["sample", *args, "--repo", "o/r", "--revision", "abc"]
            + ["--n", "8", "--out", str(sheet)]
        )
        == 0
    )
    with sheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 8
    gold = {
        "json.dumps": "EXTERNAL",
        "charge": "pkg.fees.charge",
        "fn": "UNKNOWN",
        "ledger.save": "pkg.fees.Ledger.save",
        "handlers[0]": "UNKNOWN",
        "print": "EXTERNAL",
        "Ledger": "pkg.fees.Ledger",
    }
    for row in rows:
        row["gold"] = gold[row["call_text"]]
    labelled = tmp_path / "labels.csv"
    with labelled.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=calls_eval.SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    report = tmp_path / "report.md"
    assert script.main(["score", *args, "--labels", str(labelled), "--out", str(report)]) == 0
    text = report.read_text(encoding="utf-8")
    # charge x2 and Ledger() are confident and right; ledger.save is a right guess;
    # json.dumps, fn, handlers[0] and print get no edge, which is right for each.
    assert "| confident precision | 1.000 | 3 / 3 |" in text
    assert "| unconfident precision | 1.000 | 1 / 1 |" in text
    assert "| recall, confident or not | 1.000 | 4 / 4 |" in text
    assert "| correct abstention on EXTERNAL/UNKNOWN | 1.000 | 4 / 4 |" in text
