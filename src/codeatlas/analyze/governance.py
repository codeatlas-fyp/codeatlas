"""Governance snapshot at the base commit (spec §6 `analyze.governance`, step-10).

Three jobs:

1. Parse CODEOWNERS with GitHub semantics: last matching pattern wins. The
   property test in `test_governance.py` compares to a reference implementation
   over thousands of generated patterns.

2. Parse ADR front matter (simple YAML header) so Branch 5 can match ADR
   `scope` paths against changed files for `DETERMINISTICALLY_DERIVED` links.

3. Walk the diff and emit `GovernanceChange` evidence for every file the PR
   touches that matches a glob in `policy.governance_paths`. C2 reads this list.

All functions are pure: no I/O. Inputs are already-collected text (CODEOWNERS
blob from the base commit, ADR files from the base commit, the policy that
was copied into the bundle).
"""

from __future__ import annotations

import re
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

from codeatlas.codeowners import codeowners_match
from codeatlas.codeowners import owners_for as owners_for
from codeatlas.schema import Adr, GovernanceChange, GovernanceSnapshot, OwnerRule, Policy

EXTRACTOR_VERSION = "governance@0.1.0"
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_INLINE_LIST_RE = re.compile(r"^\s*-\s+(.*?)\s*$", re.MULTILINE)


# ---- CODEOWNERS ----------------------------------------------------------


def parse_codeowners(text: str) -> list[OwnerRule]:
    """Return rules in file order. Line number is 1-indexed (useful in panels)."""
    rules: list[OwnerRule] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = line.split()
        if not tokens:
            continue
        pattern = tokens[0]
        if len(tokens) == 1:
            # GitHub allows an ownerless match to clear ownership from earlier rules.
            owners: list[str] = []
        else:
            owners = [t for t in tokens[1:] if t.startswith("@") or "@" in t]
        if len(tokens) > 1 and not owners:
            continue
        rules.append(OwnerRule(pattern=pattern, owners=owners, line=i))
    return rules


# Kept as a module alias for callers that imported the former private helper.
_codeowners_match = codeowners_match


# ---- ADR front matter ----------------------------------------------------


def parse_adr(path: str, text: str) -> Adr | None:
    """Parse an ADR file's YAML front matter. Returns None if there is none."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return None
    header = match.group(1)
    fields = _parse_simple_yaml(header)

    adr_id = str(fields.get("id") or _adr_id_from_path(path))
    status = fields.get("status")
    scope = fields.get("scope") or []
    requirement_keys = fields.get("requirement_keys") or fields.get("requirements") or []

    if isinstance(scope, str):
        scope = [scope]
    if isinstance(requirement_keys, str):
        requirement_keys = [requirement_keys]

    return Adr(
        adr_id=adr_id,
        path=path,
        status=str(status) if status is not None else None,
        scope=[str(s) for s in scope],
        requirement_keys=[str(k) for k in requirement_keys],
    )


def _adr_id_from_path(path: str) -> str:
    """`docs/adr/0007-use-tree-sitter.md` → `ADR-0007`."""
    name = PurePosixPath(path).stem
    match = re.match(r"(\d+)", name)
    return f"ADR-{match.group(1)}" if match else name


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Lightweight YAML: scalars and simple lists only. We own what ADRs we parse."""
    data: dict[str, Any] = {}
    current_key: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if _INLINE_LIST_RE.match(line) and current_key is not None:
            value = _INLINE_LIST_RE.match(line).group(1).strip("\"'")  # type: ignore[union-attr]
            data.setdefault(current_key, []).append(value)
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "" or rest == "[]":
                current_key = key
                data[key] = []
            else:
                data[key] = _coerce_scalar(rest.strip("\"'"))
                current_key = None
    return data


def _coerce_scalar(value: str) -> Any:
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.isdigit():
        return int(value)
    return value


# ---- Snapshot builder ----------------------------------------------------


def build_snapshot(
    *,
    base_sha: str,
    codeowners_text: str | None,
    adr_files: dict[str, str],
    policy_blob_sha: str | None,
    retrieved_at: datetime,
    codeowners_path: str | None = "CODEOWNERS",
) -> GovernanceSnapshot:
    """Freeze the base-commit governance state into one evidence object."""
    owner_rules: list[OwnerRule] = []
    actual_codeowners_path: str | None = None
    if codeowners_text is not None:
        owner_rules = parse_codeowners(codeowners_text)
        actual_codeowners_path = codeowners_path

    adrs: list[Adr] = []
    for path, text in sorted(adr_files.items()):
        parsed = parse_adr(path, text)
        if parsed is not None:
            adrs.append(parsed)

    return GovernanceSnapshot(
        evidence_id=f"governance:{base_sha}",
        source="analysis",
        source_ref=f"base@{base_sha}",
        retrieved_at=retrieved_at,
        extractor_version=EXTRACTOR_VERSION,
        base_sha=base_sha,
        codeowners_path=actual_codeowners_path,
        owner_rules=owner_rules,
        adrs=adrs,
        policy_blob_sha=policy_blob_sha,
    )


# ---- Governance changes in the diff --------------------------------------


def governance_changes_in_diff(
    *,
    changed_files: list[tuple[str, str]],
    policy: Policy,
    retrieved_at: datetime,
) -> list[GovernanceChange]:
    """Emit one GovernanceChange per file that matches `policy.governance_paths`.

    `changed_files` is `[(path, change_type)]` where `change_type` is one of
    "added", "modified", "deleted", "renamed" — matching the LocalRepo output.
    """
    results: list[GovernanceChange] = []
    for path, change in changed_files:
        if not any(_governance_match(glob, path) for glob in policy.governance_paths):
            continue
        results.append(
            GovernanceChange(
                evidence_id=f"governance_change:{path}",
                source="analysis",
                source_ref=f"diff:{path}",
                retrieved_at=retrieved_at,
                extractor_version=EXTRACTOR_VERSION,
                path=path,
                change_type=change,  # type: ignore[arg-type]
            )
        )
    return results


def _governance_match(glob: str, path: str) -> bool:
    """fnmatch with `**` glob support (same translation as CODEOWNERS)."""
    if "**" in glob:
        return _glob_match_double_star(glob.lstrip("/"), path)
    return fnmatchcase(path, glob) or fnmatchcase(path, glob.lstrip("/"))


def _glob_match_double_star(pattern: str, path: str) -> bool:
    """Match governance-path globs, which are separate from CODEOWNERS rules."""
    parts = pattern.split("/")
    regex_parts: list[str] = []
    for part in parts:
        if part == "**":
            regex_parts.append(".*")
        else:
            escaped = re.escape(part).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
            regex_parts.append(escaped)
    return re.fullmatch("/".join(regex_parts), path) is not None
