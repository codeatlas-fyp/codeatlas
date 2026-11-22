"""File-level evidence must survive changes without Python text hunks."""

from pathlib import Path

import pygit2

from codeatlas.bundle.canonical import canonical_bytes
from codeatlas.collect.git.repo import FileDelta, LocalRepo
from codeatlas.config import DEFAULT_POLICY
from codeatlas.evaluate.rules import default_rules, ruleset_version
from codeatlas.evaluate.rules.c5_owner_did_not_approve import C5CompleteFileInventory
from codeatlas.evaluate.verdict import decide
from codeatlas.pipeline import _governance as collect_governance
from codeatlas.schema import ChangedFile, EvidenceBundle
from tests.unit.test_c5 import NOW, _bundle, _governance, _review


def _delta(old: str | None, new: str | None, change: str = "modified") -> ChangedFile:
    return ChangedFile(
        old_path=old,
        new_path=new,
        change_type=change,
        old_mode=33188,
        new_mode=33188,
        old_oid="base",
        new_oid="head",
    )


def test_mixed_file_owners_are_both_checked() -> None:
    bundle = _bundle(
        governance=_governance("*.py @python-owner\n*.md @docs-owner\n"),
        reviews=[_review("python-owner", "APPROVED", "head")],
        commits=[],
        changed=[],
    ).model_copy(update={"changed_files": [_delta("a.py", "a.py"), _delta("a.md", "a.md")]})
    result = C5CompleteFileInventory().check(bundle, [], bundle.policy)
    assert result.outcome == "VIOLATED"
    assert decide([result]) == "FAIL"
    assert "a.md" in result.message


def test_old_bundle_serialization_and_c5_version_are_preserved() -> None:
    bundle = _bundle(governance=_governance("* @owner"), reviews=[], commits=[], changed=[])
    data = bundle.model_dump(exclude={"changed_files"})
    legacy = EvidenceBundle.model_validate(data)
    assert b'"changed_files"' not in canonical_bytes(legacy)
    assert "C5@2" in ruleset_version(default_rules())
    assert "C5@3" in ruleset_version(default_rules(complete_file_inventory=True))


def test_unavailable_file_inventory_is_insufficient_evidence() -> None:
    bundle = _bundle(governance=_governance(None), reviews=[], commits=[], changed=[])
    bundle = bundle.model_copy(update={"changed_files": None})
    result = C5CompleteFileInventory().check(bundle, [], bundle.policy)
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert decide([result]) == "UNKNOWN"
    assert b'"changed_files":null' in canonical_bytes(bundle)


def test_governance_mode_change_is_seen_without_text_hunks() -> None:
    class Git:
        def file_bytes(self, revision: str, path: str) -> bytes | None:
            return b"* @owner\n" if path == "CODEOWNERS" else None

        def list_files(self, revision: str, prefix: str = "") -> list[str]:
            return []

    bundle = _bundle(governance=_governance(None), reviews=[], commits=[], changed=[])
    delta = FileDelta("CODEOWNERS", "CODEOWNERS", "modified", 33188, 33261, "id", "id")
    _, changes, _ = collect_governance(bundle.change, [], Git(), DEFAULT_POLICY, NOW, [delta])
    assert [item.path for item in changes] == ["CODEOWNERS"]


def test_git_delta_inventory_includes_binary_rename_delete_and_mode(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path), bare=True)
    sig = pygit2.Signature("Test", "test@example.com", 1_760_000_000, 0)

    def write_tree(files: dict[str, tuple[bytes, int]]) -> pygit2.Oid:
        tree = repo.TreeBuilder()
        for path, (body, mode) in files.items():
            tree.insert(path, repo.create_blob(body), mode)
        return tree.write()

    regular, executable = pygit2.GIT_FILEMODE_BLOB, pygit2.GIT_FILEMODE_BLOB_EXECUTABLE
    before = {
        "data.bin": (b"\0old", regular),
        "old.py": (b"print(1)\n", regular),
        "gone.md": (b"removed\n", regular),
        "mode.py": (b"x=1\n", regular),
    }
    after = {
        "data.bin": (b"\0new", regular),
        "new.py": (b"print(1)\n", regular),
        "mode.py": (b"x=1\n", executable),
    }
    base = repo.create_commit(None, sig, sig, "base", write_tree(before), [])
    head = repo.create_commit(None, sig, sig, "head", write_tree(after), [base])
    source = LocalRepo(tmp_path)
    deltas = source.diff_files(str(base), str(head))
    assert {(x.old_path, x.new_path, x.change_type) for x in deltas} == {
        ("data.bin", "data.bin", "modified"),
        ("old.py", "new.py", "renamed"),
        ("gone.md", None, "deleted"),
        ("mode.py", "mode.py", "modified"),
    }
    mode = next(x for x in deltas if x.old_path == "mode.py")
    assert mode.old_oid == mode.new_oid and mode.old_mode != mode.new_mode
    assert {h.file_path for h in source.diff_hunks(str(base), str(head))} == {"gone.md"}
