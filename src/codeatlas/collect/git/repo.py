"""Local git access with pygit2 (spec §6 `collect.git`, step-9).

Three operations the pipeline needs. All read-only. Does not checkout, does
not modify the working tree, does not fetch.

    1. merge_base(base_ref, head_sha) -> str
       The commit where the PR branched off its base. Needed by C1 and C6.

    2. diff_hunks(base_sha, head_sha) -> list[DiffHunk]
       Per-file hunk ranges. Branch 4 (`analyze.code`) maps hunks to Python
       entities; C2 and C5 only need the file paths.

    3. file_bytes(sha, path) -> bytes | None
       Content at a given commit. The governance snapshot reads CODEOWNERS,
       policy.yaml and ADRs through this at the base commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygit2


@dataclass(frozen=True)
class DiffHunk:
    """One hunk from `git diff base..head` — the line range at head side.

    `old_start`/`old_lines` describe the base side (0, 0 for pure additions);
    `new_start`/`new_lines` describe the head side (0, 0 for pure deletions).
    `change_type` is set from pygit2's deltas.
    """

    file_path: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    change_type: str  # "added" | "modified" | "deleted" | "renamed"


@dataclass(frozen=True)
class FileDelta:
    """A Git file change independent of whether a textual hunk exists."""

    old_path: str | None
    new_path: str | None
    change_type: str
    old_mode: int
    new_mode: int
    old_oid: str
    new_oid: str


class LocalRepo:
    """Thin wrapper around pygit2.Repository with the three ops above."""

    def __init__(self, path: Path) -> None:
        import pygit2  # imported here so tests without pygit2 can skip

        self._path = path
        self._repo: pygit2.Repository = pygit2.Repository(str(path))

    def merge_base(self, base_ref: str, head_sha: str) -> str:
        """Return the merge-base SHA between the base ref (branch name or SHA) and head."""
        base_oid = self._resolve(base_ref)
        head_oid = self._resolve(head_sha)
        mb = self._repo.merge_base(base_oid, head_oid)
        if mb is None:
            raise ValueError(f"no merge-base between {base_ref} and {head_sha}")
        return str(mb)

    def diff_hunks(self, base_sha: str, head_sha: str, *, context_lines: int = 3) -> list[DiffHunk]:
        """Return hunks of the diff between base and head, flattened per hunk.

        `context_lines=0` gives hunks that cover only changed lines (what entity mapping needs).
        """

        base_commit = self._repo.get(self._resolve(base_sha))
        head_commit = self._repo.get(self._resolve(head_sha))
        if base_commit is None or head_commit is None:
            raise ValueError("base or head commit not in local repo")

        diff = self._repo.diff(base_commit.id, head_commit.id, context_lines=context_lines)
        diff.find_similar()  # populate rename info (status 'R')

        hunks: list[DiffHunk] = []
        for patch in diff:
            if patch is None:
                continue
            delta = patch.delta
            file_path = delta.new_file.path or delta.old_file.path
            change_type = _change_type(delta.status)
            for hunk in patch.hunks:
                hunks.append(
                    DiffHunk(
                        file_path=file_path,
                        old_start=hunk.old_start,
                        old_lines=hunk.old_lines,
                        new_start=hunk.new_start,
                        new_lines=hunk.new_lines,
                        change_type=change_type,
                    )
                )
        return hunks

    def diff_files(self, base_sha: str, head_sha: str) -> list[FileDelta]:
        """Return every file delta, including binary, rename-only and mode changes."""
        base = self._repo.get(self._resolve(base_sha))
        head = self._repo.get(self._resolve(head_sha))
        if base is None or head is None:
            raise ValueError("base or head commit not in local repo")
        diff = self._repo.diff(base.id, head.id, context_lines=0)
        diff.find_similar()
        result = []
        for delta in diff.deltas:
            change = _change_type(delta.status)
            result.append(
                FileDelta(
                    old_path=None if change == "added" else delta.old_file.path,
                    new_path=None if change == "deleted" else delta.new_file.path,
                    change_type=change,
                    old_mode=delta.old_file.mode,
                    new_mode=delta.new_file.mode,
                    old_oid=str(delta.old_file.id),
                    new_oid=str(delta.new_file.id),
                )
            )
        return result

    def file_bytes(self, sha: str, path: str) -> bytes | None:
        """Raw bytes of `path` at commit `sha`, or None if absent at that commit."""
        import pygit2

        commit = self._repo.get(self._resolve(sha))
        if commit is None:
            return None
        try:
            entry = commit.tree[path]
        except KeyError:
            return None
        obj = self._repo[entry.id]
        if not isinstance(obj, pygit2.Blob):
            return None
        return bytes(obj.data)

    def changed_paths(self, base_sha: str, head_sha: str) -> list[str]:
        """Convenience: unique file paths in the diff. C2 only needs these."""
        seen: set[str] = set()
        order: list[str] = []
        for delta in self.diff_files(base_sha, head_sha):
            path = delta.new_path or delta.old_path
            if path and path not in seen:
                seen.add(path)
                order.append(path)
        return order

    def list_files(self, sha: str, prefix: str = "") -> list[str]:
        """Every file path at commit `sha` under folder `prefix` (sorted). Used to find ADRs."""
        import pygit2

        commit = self._repo.get(self._resolve(sha))
        if commit is None:
            return []
        tree = commit.tree
        folder = prefix.strip("/")
        if folder:
            try:
                entry = tree[folder]
            except KeyError:
                return []
            sub = self._repo[entry.id]
            if not isinstance(sub, pygit2.Tree):
                return []
            tree = sub
        paths: list[str] = []
        stack: list[tuple[str, pygit2.Tree]] = [(folder, tree)]
        while stack:
            base, current = stack.pop()
            for item in current:
                path = f"{base}/{item.name}" if base else str(item.name)
                obj = self._repo[item.id]
                if isinstance(obj, pygit2.Tree):
                    stack.append((path, obj))
                elif isinstance(obj, pygit2.Blob):
                    paths.append(path)
        return sorted(paths)

    def resolve(self, ref: str) -> str:
        """Full SHA of a branch name, tag or (short) SHA, so a run can be pinned to it."""
        return self._resolve(ref)

    def python_sources(self, sha: str) -> dict[str, bytes]:
        """Every `.py` file at commit `sha`, path -> bytes (the semantic corpus input)."""
        out: dict[str, bytes] = {}
        for path in self.list_files(sha):
            if path.endswith(".py"):
                data = self.file_bytes(sha, path)
                if data is not None:
                    out[path] = data
        return out

    def _resolve(self, ref: str) -> str:
        """Accept a branch name, tag, or SHA. Returns the SHA as a string."""
        obj = self._repo.revparse_single(ref)
        return str(obj.id)


def _change_type(status: int) -> str:
    """Translate pygit2's delta status enum into our schema's literal."""
    import pygit2

    mapping = {
        pygit2.GIT_DELTA_ADDED: "added",
        pygit2.GIT_DELTA_MODIFIED: "modified",
        pygit2.GIT_DELTA_DELETED: "deleted",
        pygit2.GIT_DELTA_RENAMED: "renamed",
        pygit2.GIT_DELTA_COPIED: "modified",
        pygit2.GIT_DELTA_TYPECHANGE: "modified",
    }
    return mapping.get(status, "modified")
