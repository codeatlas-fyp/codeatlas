"""Regression tests for finding A-1: replay must refuse edited verdict fields."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from codeatlas.bundle.replay import replay
from codeatlas.errors import ReplayMismatchError
from codeatlas.pipeline import evaluate_bundle

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bundles"


@pytest.fixture
def sample_copy(tmp_path: Path) -> Path:
    (source,) = [p for p in FIXTURES.iterdir() if p.is_dir()]
    target = tmp_path / source.name
    shutil.copytree(source, target)
    return target


def _edit_verdict(folder: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    file = folder / "verdict.json"
    data: dict[str, Any] = json.loads(file.read_text())
    edit(data)
    file.write_text(json.dumps(data))


def test_untampered_sample_replays_with_match(sample_copy: Path) -> None:
    result = replay(sample_copy, evaluate_bundle)
    assert result.match


def test_changed_value_is_refused(sample_copy: Path) -> None:
    # The exact A-1 reproduction: FAIL -> PASS, nothing else changed.
    def flip(data: dict[str, Any]) -> None:
        data["value"] = "PASS" if data["value"] != "PASS" else "FAIL"

    _edit_verdict(sample_copy, flip)
    with pytest.raises(ReplayMismatchError, match="verdict.json was altered"):
        replay(sample_copy, evaluate_bundle)


def test_changed_result_is_refused(sample_copy: Path) -> None:
    def reword(data: dict[str, Any]) -> None:
        data["results"][0]["message"] = data["results"][0]["message"] + " (edited)"

    _edit_verdict(sample_copy, reword)
    with pytest.raises(ReplayMismatchError, match="verdict.json was altered"):
        replay(sample_copy, evaluate_bundle)


def test_changed_ruleset_version_is_refused(sample_copy: Path) -> None:
    _edit_verdict(sample_copy, lambda data: data.update(ruleset_version="999"))
    with pytest.raises(ReplayMismatchError, match="verdict.json was altered"):
        replay(sample_copy, evaluate_bundle)


def test_rewritten_hash_that_disagrees_with_the_bundle_is_refused(sample_copy: Path) -> None:
    # Edit the value AND recompute verdict_hash so the self-check passes; replay must then
    # still refuse, because the recomputed verdict from the bundle is different.
    from codeatlas.bundle.hashing import verdict_hash
    from codeatlas.schema import Verdict

    file = sample_copy / "verdict.json"
    stored = Verdict.model_validate_json(file.read_text())
    flipped = "PASS" if stored.value != "PASS" else "FAIL"
    forged_hash = verdict_hash(stored.bundle_hash, stored.ruleset_version, stored.results, flipped)
    data: dict[str, Any] = json.loads(file.read_text())
    data["value"] = flipped
    data["verdict_hash"] = forged_hash
    file.write_text(json.dumps(data))
    with pytest.raises(ReplayMismatchError, match="verdict_hash differs"):
        replay(sample_copy, evaluate_bundle)
