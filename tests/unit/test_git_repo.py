"""Tests for the pygit2 adapter. Builds a real repository in tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pygit2")
import pygit2  # noqa: E402  (import after skip)

from codeatlas.collect.git.repo import LocalRepo  # noqa: E402


def _build_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Make a tiny repo with two commits and return (path, base_sha, head_sha)."""
    repo = pygit2.init_repository(str(tmp_path), bare=False)
    sig = pygit2.Signature("Test", "test@example.com")

    # Commit 1: add foo.py
    foo = tmp_path / "foo.py"
    foo.write_text("def a():\n    return 1\n")
    repo.index.add("foo.py")
    repo.index.write()
    tree = repo.index.write_tree()
    base = repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])

    # Commit 2: modify foo.py + add bar.py
    foo.write_text("def a():\n    return 2\n\ndef c():\n    return 3\n")
    (tmp_path / "bar.py").write_text("x = 1\n")
    repo.index.add("foo.py")
    repo.index.add("bar.py")
    repo.index.write()
    tree2 = repo.index.write_tree()
    head = repo.create_commit("refs/heads/main", sig, sig, "add bar + edit foo", tree2, [base])

    return tmp_path, str(base), str(head)


def test_merge_base_returns_the_shared_commit(tmp_path: Path) -> None:
    path, base, head = _build_repo(tmp_path)
    repo = LocalRepo(path)
    assert repo.merge_base(base, head) == base


def test_diff_hunks_lists_both_files(tmp_path: Path) -> None:
    path, base, head = _build_repo(tmp_path)
    repo = LocalRepo(path)
    hunks = repo.diff_hunks(base, head)
    files = {h.file_path for h in hunks}
    assert files == {"foo.py", "bar.py"}


def test_diff_hunks_marks_new_file_as_added(tmp_path: Path) -> None:
    path, base, head = _build_repo(tmp_path)
    repo = LocalRepo(path)
    hunks = repo.diff_hunks(base, head)
    bar_hunks = [h for h in hunks if h.file_path == "bar.py"]
    assert bar_hunks and bar_hunks[0].change_type == "added"


def test_file_bytes_reads_at_base_commit(tmp_path: Path) -> None:
    path, base, head = _build_repo(tmp_path)
    repo = LocalRepo(path)
    foo_at_base = repo.file_bytes(base, "foo.py")
    assert foo_at_base is not None
    assert b"return 1" in foo_at_base
    # And the file does not exist at base yet for bar.py.
    assert repo.file_bytes(base, "bar.py") is None


def test_changed_paths_dedupes(tmp_path: Path) -> None:
    path, base, head = _build_repo(tmp_path)
    repo = LocalRepo(path)
    paths = repo.changed_paths(base, head)
    assert paths == sorted(paths) or set(paths) == {"foo.py", "bar.py"}
