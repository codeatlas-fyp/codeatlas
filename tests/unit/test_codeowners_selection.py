"""Verify the application selects the highest-priority CODEOWNERS file."""

from datetime import UTC, datetime

from codeatlas.config import DEFAULT_POLICY
from codeatlas.pipeline import _governance
from codeatlas.schema import ChangeRef


def test_github_codeowners_wins_over_root_and_docs() -> None:
    class GitFiles:
        def file_bytes(self, revision, path):
            return {
                ".github/CODEOWNERS": b"* @github-owner\n",
                "CODEOWNERS": b"* @root-owner\n",
                "docs/CODEOWNERS": b"* @docs-owner\n",
            }.get(path)

        def list_files(self, revision, path):
            return []

    change = ChangeRef(
        repo="x/y", pr_number=1, base_sha="base", head_sha="head", merge_base_sha="base"
    )
    snapshot, _, _ = _governance(change, [], GitFiles(), DEFAULT_POLICY, datetime.now(UTC))
    assert snapshot.codeowners_path == ".github/CODEOWNERS"
    assert snapshot.owner_rules[0].owners == ["@github-owner"]
