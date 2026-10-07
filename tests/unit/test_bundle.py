"""Tests for docs/specs/step-3-bundle-replay.md (AC1, AC4-AC9)."""

import json
import shutil
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any

import pytest
from codeatlas.bundle.canonical import canonical_bytes, has_identifier
from codeatlas.bundle.hashing import bundle_hash, sha256_hex, verdict_hash
from codeatlas.bundle.replay import replay
from codeatlas.bundle.store import BundleStore, load_bundle, load_verdict
from codeatlas.errors import (
    CodeAtlasError,
    CollectionError,
    ConfigError,
    EvidenceInvalid,
    EvidenceValidationError,
    ReplayMismatch,
    ReplayMismatchError,
    SourceUnavailable,
)
from pydantic import BaseModel

import codeatlas.schema as schema
from codeatlas.schema import CheckResult, EvidenceBundle, Verdict, VerdictValue
from tests.samples import BUNDLE, CHECK

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bundles"
# Golden hash of the committed sample bundle. Computed once from scripts/make_sample_bundle.py;
# it must be identical on Windows and on the Linux CI runner (AC4, "2 OS images").
SAMPLE_HASH = "SAMPLE_HASH_PLACEHOLDER"
RULESET = "test-ruleset"


def evaluator(value: VerdictValue, results: list[CheckResult]) -> Any:
    def evaluate(bundle: EvidenceBundle, hash_: str) -> Verdict:
        return Verdict(
            value=value,
            results=results,
            ruleset_version=RULESET,
            bundle_hash=hash_,
            verdict_hash=verdict_hash(hash_, RULESET, results, value),
        )

    return evaluate


def stored(tmp_path: Path) -> tuple[Path, str]:
    """A store holding BUNDLE and a FAIL verdict over it."""
    store = BundleStore(tmp_path / "store")
    hash_ = store.save(BUNDLE)
    store.save_verdict(evaluator("FAIL", [CHECK])(BUNDLE, hash_))
    return store.root / hash_, hash_


# --- AC1: identical bytes in separate processes ---------------------------------------------


def test_ac1_canonical_bytes_identical_in_two_processes() -> None:
    code = (
        "import sys; from tests.samples import BUNDLE; "
        "from codeatlas.bundle.canonical import canonical_bytes; "
        "sys.stdout.buffer.write(canonical_bytes(BUNDLE))"
    )
    runs = [
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, check=True)
        for _ in range(2)
    ]

    assert runs[0].stdout == runs[1].stdout == canonical_bytes(BUNDLE)


# --- AC4: golden hash of the committed sample ----------------------------------------------


def test_ac4_sample_bundle_matches_golden_hash() -> None:
    folder = FIXTURES / SAMPLE_HASH

    bundle = load_bundle(folder)

    assert bundle_hash(bundle) == SAMPLE_HASH
    assert (folder / "bundle.json").read_bytes() == canonical_bytes(BUNDLE)


# --- AC5: tampering is refused with exit code 5 --------------------------------------------


def test_ac5_one_changed_byte_is_refused(tmp_path: Path) -> None:
    folder, _ = stored(tmp_path)
    path = folder / "bundle.json"
    path.write_bytes(path.read_bytes().replace(b"SBX-2", b"SBX-3", 1))

    with pytest.raises(ReplayMismatchError, match="hash") as raised:
        load_bundle(folder)
    with pytest.raises(ReplayMismatchError):
        replay(folder, evaluator("FAIL", [CHECK]))
    assert raised.value.exit_code == 5


def test_ac5_invalid_json_is_refused(tmp_path: Path) -> None:
    raw = b'{"schema_version": "0",'
    folder = tmp_path / sha256_hex(raw)
    folder.mkdir()
    (folder / "bundle.json").write_bytes(raw)

    with pytest.raises(ReplayMismatchError, match="not a valid bundle"):
        load_bundle(folder)


def test_ac5_non_canonical_bytes_are_refused(tmp_path: Path) -> None:
    raw = json.dumps(json.loads(canonical_bytes(BUNDLE)), indent=2).encode()
    folder = tmp_path / sha256_hex(raw)
    folder.mkdir()
    (folder / "bundle.json").write_bytes(raw)

    with pytest.raises(ReplayMismatchError, match="canonical"):
        load_bundle(folder)


# --- AC6: replay -------------------------------------------------------------------------------


def test_ac6_replay_with_same_evaluator_matches(tmp_path: Path) -> None:
    folder, hash_ = stored(tmp_path)

    result = replay(folder / "bundle.json", evaluator("FAIL", [CHECK]))

    assert result.match
    assert result.bundle_hash == hash_
    assert result.stored_verdict_hash == result.recomputed_verdict_hash


def test_ac6_replay_with_different_result_is_refused(tmp_path: Path) -> None:
    folder, _ = stored(tmp_path)

    with pytest.raises(ReplayMismatchError, match="verdict_hash"):
        replay(folder, evaluator("PASS", []))


def test_ac6_replay_reports_mismatch_without_raising_when_not_strict(tmp_path: Path) -> None:
    folder, _ = stored(tmp_path)

    result = replay(folder, evaluator("PASS", []), strict=False)

    assert not result.match


def test_ac6_verdict_for_another_bundle_is_refused(tmp_path: Path) -> None:
    folder, _ = stored(tmp_path)
    other = Verdict(
        value="PASS", results=[], ruleset_version=RULESET, bundle_hash="0" * 64, verdict_hash="1"
    )
    (folder / "verdict.json").write_bytes(canonical_bytes(other))

    with pytest.raises(ReplayMismatchError, match="another bundle"):
        replay(folder, evaluator("FAIL", [CHECK]))


def test_ac6_missing_verdict_is_refused(tmp_path: Path) -> None:
    store = BundleStore(tmp_path)
    hash_ = store.save(BUNDLE)

    with pytest.raises(ReplayMismatchError, match="no stored verdict"):
        replay(tmp_path / hash_, evaluator("FAIL", [CHECK]))


def test_ac6_saved_verdict_round_trips(tmp_path: Path) -> None:
    folder, hash_ = stored(tmp_path)

    assert load_verdict(folder) == evaluator("FAIL", [CHECK])(BUNDLE, hash_)


def test_ac6_verdict_needs_its_bundle_in_the_store(tmp_path: Path) -> None:
    verdict = evaluator("PASS", [])(BUNDLE, "0" * 64)

    with pytest.raises(EvidenceValidationError, match="no bundle"):
        BundleStore(tmp_path).save_verdict(verdict)


# --- AC7: never partial --------------------------------------------------------------------


def test_ac7_failed_write_leaves_no_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("codeatlas.bundle.store.os.replace", fail)
    store = BundleStore(tmp_path)

    with pytest.raises(OSError, match="disk full"):
        store.save(BUNDLE)

    assert list(tmp_path.rglob("bundle.json")) == []
    assert list(tmp_path.rglob("*.tmp")) == []


def test_ac7_saving_twice_is_idempotent(tmp_path: Path) -> None:
    store = BundleStore(tmp_path)

    assert store.save(BUNDLE) == store.save(BUNDLE) == bundle_hash(BUNDLE)


# --- AC8: byte format ------------------------------------------------------------------------


def test_ac8_floats_datetimes_and_spacing() -> None:
    text = canonical_bytes(BUNDLE).decode()

    assert '"fused_score":"0.0328"' in text
    assert '"collected_at":"2026-10-04T04:00:00Z"' in text
    assert ", " not in text.replace('", "', "")  # no separator spaces
    assert not text.endswith("\n")


def test_ac8_float_rounding_and_sub_second_truncation() -> None:
    candidate = BUNDLE.candidates[0].model_copy(update={"fused_score": 0.123456})
    event = BUNDLE.change_events[0]
    later = event.model_copy(update={"at": event.at.replace(microsecond=999_999)})

    assert b'"fused_score":"0.1235"' in canonical_bytes(candidate)
    assert canonical_bytes(later) == canonical_bytes(event)


def test_ac8_lists_sorted_by_identifier_and_tuples_kept() -> None:
    rules = [
        schema.OwnerRule(pattern="/b/", owners=["@z", "@a"], line=10),
        schema.OwnerRule(pattern="/a/", owners=["@a"], line=9),
    ]

    data = json.loads(canonical_bytes(rules))

    assert [r["line"] for r in data] == [9, 10]  # numeric order, not text order
    assert data[1]["owners"] == ["@a", "@z"]
    assert json.loads(canonical_bytes(BUNDLE.changed[0]))["hunk_lines"] == [12, 18]


def test_ac8_naive_datetime_is_rejected() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="naive"):
        canonical_bytes({"at": datetime(2026, 1, 1)})  # noqa: DTZ001


# --- AC9: every list element type has an identifier -----------------------------------------


def _list_element_models() -> set[type[BaseModel]]:
    found: set[type[BaseModel]] = set()
    for name in schema.__all__:
        model = getattr(schema, name)
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for field in model.model_fields.values():
            if typing.get_origin(field.annotation) is list:
                (element,) = typing.get_args(field.annotation)
                if isinstance(element, type) and issubclass(element, BaseModel):
                    found.add(element)
    return found


def test_ac9_every_list_element_model_has_an_identifier() -> None:
    elements = _list_element_models()

    assert len(elements) >= 15
    assert [m.__name__ for m in elements if not has_identifier(m)] == []


# --- verdict hash -----------------------------------------------------------------------------


def test_verdict_hash_depends_on_every_input() -> None:
    base = verdict_hash("a" * 64, "1", [CHECK], "FAIL")

    assert base == verdict_hash("a" * 64, "1", [CHECK], "FAIL")
    assert len(base) == 64
    assert base != verdict_hash("b" * 64, "1", [CHECK], "FAIL")
    assert base != verdict_hash("a" * 64, "2", [CHECK], "FAIL")
    assert base != verdict_hash("a" * 64, "1", [], "FAIL")
    assert base != verdict_hash("a" * 64, "1", [CHECK], "REVIEW")


# --- errors (§7.8, §15) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CollectionError("jira", status=500, retryable=True), 3),
        (SourceUnavailable("jira", status=None, retryable=True), 3),
        (ConfigError("missing key"), 4),
        (EvidenceValidationError("bad bundle"), 4),
        (ReplayMismatchError("tampered"), 5),
    ],
)
def test_error_exit_codes(error: CodeAtlasError, code: int) -> None:
    assert error.exit_code == code


def test_error_aliases_and_collection_fields() -> None:
    error = CollectionError("github", status=404, retryable=False)

    assert EvidenceInvalid is EvidenceValidationError
    assert ReplayMismatch is ReplayMismatchError
    assert (error.source, error.status, error.retryable) == ("github", 404, False)
    assert "github" in str(error)


def test_fixture_folder_holds_only_hash_named_bundles() -> None:
    folders = [p for p in FIXTURES.iterdir() if p.is_dir()]

    assert folders
    for folder in folders:
        assert folder.name == sha256_hex((folder / "bundle.json").read_bytes())


def test_store_paths(tmp_path: Path) -> None:
    store = BundleStore(tmp_path)
    hash_ = store.save(BUNDLE)

    assert store.folder(hash_) == tmp_path / hash_
    assert load_bundle(store.folder(hash_) / "bundle.json") == load_bundle(store.folder(hash_))
    shutil.rmtree(tmp_path / hash_)
    with pytest.raises(ReplayMismatchError, match="no bundle"):
        load_bundle(tmp_path / hash_)
