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

    def diff_hunks(self, base_sha: str, head_sha: str) -> list[DiffHunk]:
        """Return hunks of the diff between base and head, flattened per hunk."""
        import pygit2

        base_commit = self._repo.get(self._resolve(base_sha))
        head_commit = self._repo.get(self._resolve(head_sha))
        if base_commit is None or head_commit is None:
            raise ValueError("base or head commit not in local repo")

        diff = self._repo.diff(base_commit.tree, head_commit.tree)
        diff.find_similar()  # populate rename info (status 'R')

        hunks: list[DiffHunk] = []
        for patch in diff:
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

    def file_bytes(self, sha: str, path: str) -> bytes | None:
        """Raw bytes of `path` at commit `sha`, or None if absent at that commit."""
        commit = self._repo.get(self._resolve(sha))
        if commit is None:
            return None
        try:
            entry = commit.tree[path]  # type: ignore[index]
        except KeyError:
            return None
        blob = self._repo[entry.id]
        return bytes(blob.data)

    def changed_paths(self, base_sha: str, head_sha: str) -> list[str]:
        """Convenience: unique file paths in the diff. C2 only needs these."""
        seen: set[str] = set()
        order: list[str] = []
        for hunk in self.diff_hunks(base_sha, head_sha):
            if hunk.file_path not in seen:
                seen.add(hunk.file_path)
                order.append(hunk.file_path)
        return order

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
