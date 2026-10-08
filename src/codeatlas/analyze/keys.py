"""Work-item key finder (spec §5 stage 3, step-9; FR via Case C12-equivalent).

Finds every `[A-Z]+-\\d+` in four places and records *where* each was found so
C12-style "no key" and C9 "code changed by keyless commit" can inspect the
source of each ref:

    branch name      -> found_in = "branch"
    PR title         -> found_in = "pr_title"
    PR body          -> found_in = "pr_body"
    commit message   -> found_in = "commit"     commit_sha = that commit

Returns `list[WorkItemRef]` matching the schema model exactly — including the
required `evidence_id`, `source`, `source_ref`, `retrieved_at`, and
`extractor_version` fields on each.

Pure function: no I/O, no clock reads. The caller passes `retrieved_at` so
replay is reproducible.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from codeatlas.schema import WorkItemRef

DEFAULT_PATTERN = r"[A-Z]+-\d+"
EXTRACTOR_VERSION = "keys@0.1.0"


def find_keys(
    *,
    branch: str,
    pr_title: str,
    pr_body: str,
    commits: list[dict[str, Any]],
    pattern: str = DEFAULT_PATTERN,
    retrieved_at: datetime,
) -> list[WorkItemRef]:
    """Scan every source once and return deduplicated WorkItemRef evidence.

    Deduplication rule: (key, found_in, commit_sha) is the identity. The same
    `SBX-4` showing up in the branch name and in two commits yields three refs,
    not one.
    """
    rx = re.compile(pattern)
    refs: list[WorkItemRef] = []
    seen: set[tuple[str, str, str | None]] = set()

    def add(key: str, found_in: str, commit_sha: str | None, source_ref: str) -> None:
        tag = (key, found_in, commit_sha)
        if tag in seen:
            return
        seen.add(tag)
        refs.append(
            WorkItemRef(
                evidence_id=f"keys:{found_in}:{commit_sha or '-'}:{key}",
                source="analysis",
                source_ref=source_ref,
                retrieved_at=retrieved_at,
                extractor_version=EXTRACTOR_VERSION,
                key=key,
                found_in=found_in,  # type: ignore[arg-type]
                commit_sha=commit_sha,
            )
        )

    for match in rx.findall(branch or ""):
        add(match, "branch", None, f"branch:{branch}")
    for match in rx.findall(pr_title or ""):
        add(match, "pr_title", None, "pr:title")
    for match in rx.findall(pr_body or ""):
        add(match, "pr_body", None, "pr:body")
    for commit in commits:
        sha = str(commit.get("sha", ""))
        message = _commit_message(commit)
        if not sha or not message:
            continue
        for match in rx.findall(message):
            add(match, "commit", sha, f"commit:{sha}")

    return refs


def _commit_message(commit: dict[str, Any]) -> str:
    """GitHub returns `commit.message` nested; pygit2 returns top-level."""
    nested = commit.get("commit")
    if isinstance(nested, dict) and isinstance(nested.get("message"), str):
        return nested["message"]
    msg = commit.get("message")
    return msg if isinstance(msg, str) else ""


def keys_only(refs: list[WorkItemRef]) -> list[str]:
    """Convenience: unique sorted list of distinct keys mentioned anywhere."""
    return sorted({ref.key for ref in refs})
