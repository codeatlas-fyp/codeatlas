"""Tests for `analyze.governance` — CODEOWNERS, ADR, governance-change detection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codeatlas.analyze.governance import (
    build_snapshot,
    governance_changes_in_diff,
    owners_for,
    parse_adr,
    parse_codeowners,
)
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    Policy,
    SemanticPolicy,
)

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

CODEOWNERS_SAMPLE = """\
# comment line, ignored

*                    @yusra
/books.py            @saleha
/payments/           @areej
/payments/fees.py    @areej @yusra
"""


def _policy(governance_paths: list[str]) -> Policy:
    return Policy(
        version=1,
        workitem_key_pattern=r"[A-Z]+-\d+",
        requirement_fields=["summary", "description", "acceptance_criteria"],
        governance_paths=governance_paths,
        approval=ApprovalPolicy(status="Approved", approver_account_ids=["acct-1"]),
        priority_authority=AuthorityPolicy(account_ids=["acct-1"]),
        cases={},
        semantic=SemanticPolicy(top_k=5),
    )


def test_parse_codeowners_strips_comments_and_blank_lines() -> None:
    rules = parse_codeowners(CODEOWNERS_SAMPLE)
    assert len(rules) == 4
    assert rules[0].pattern == "*"
    assert rules[0].owners == ["@yusra"]
    assert rules[0].line == 3


def test_last_match_wins_default_catch_all() -> None:
    rules = parse_codeowners(CODEOWNERS_SAMPLE)
    assert owners_for("README.md", rules) == ["@yusra"]
    assert owners_for("books.py", rules) == ["@saleha"]
    assert owners_for("payments/fees.py", rules) == ["@areej", "@yusra"]


def test_last_match_wins_overrides_earlier_entry() -> None:
    """The spec's property test: later patterns override earlier ones.

    Here `/payments/` sets owner to Areej; then `/payments/fees.py` sets it
    to Areej + Yusra. The final pattern is what matters.
    """
    rules = parse_codeowners(CODEOWNERS_SAMPLE)
    assert owners_for("payments/fees.py", rules) == ["@areej", "@yusra"]
    assert owners_for("payments/other.py", rules) == ["@areej"]


def test_no_matching_pattern_returns_empty_list() -> None:
    rules = parse_codeowners("/explicit.py @yusra\n")
    assert owners_for("other.py", rules) == []


def test_parse_adr_pulls_scope_and_requirement_keys() -> None:
    adr_text = """\
---
id: ADR-0007
status: Accepted
scope:
  - "payments/**"
  - "loans.py"
requirement_keys:
  - SBX-4
---

# Decision

We adopt per-day caps.
"""
    adr = parse_adr("docs/adr/0007-caps.md", adr_text)
    assert adr is not None
    assert adr.adr_id == "ADR-0007"
    assert adr.status == "Accepted"
    assert "payments/**" in adr.scope
    assert adr.requirement_keys == ["SBX-4"]


def test_adr_without_front_matter_returns_none() -> None:
    assert parse_adr("docs/adr/notes.md", "just prose, no yaml") is None


def test_adr_id_defaults_to_filename_number() -> None:
    adr = parse_adr(
        "docs/adr/0012-example.md",
        "---\nstatus: Proposed\n---\nbody\n",
    )
    assert adr is not None
    assert adr.adr_id == "ADR-0012"


def test_build_snapshot_collects_everything() -> None:
    snapshot = build_snapshot(
        base_sha="base123",
        codeowners_text=CODEOWNERS_SAMPLE,
        adr_files={
            "docs/adr/0001.md": "---\nid: ADR-0001\nstatus: Accepted\n---\nx",
        },
        policy_blob_sha="policy-sha",
        retrieved_at=NOW,
    )
    assert snapshot.base_sha == "base123"
    assert snapshot.codeowners_path == "CODEOWNERS"
    assert len(snapshot.owner_rules) == 4
    assert len(snapshot.adrs) == 1
    assert snapshot.adrs[0].adr_id == "ADR-0001"
    assert snapshot.policy_blob_sha == "policy-sha"


def test_build_snapshot_without_codeowners_leaves_path_none() -> None:
    snapshot = build_snapshot(
        base_sha="base123",
        codeowners_text=None,
        adr_files={},
        policy_blob_sha=None,
        retrieved_at=NOW,
    )
    assert snapshot.codeowners_path is None
    assert snapshot.owner_rules == []


def test_governance_changes_match_configured_paths() -> None:
    policy = _policy(["CODEOWNERS", ".github/workflows/**", ".codeatlas/**"])
    changed = [
        ("CODEOWNERS", "modified"),
        ("src/foo.py", "modified"),
        (".github/workflows/ci.yml", "modified"),
        (".codeatlas/policy.yaml", "added"),
    ]
    changes = governance_changes_in_diff(
        changed_files=changed, policy=policy, retrieved_at=NOW,
    )
    paths = {c.path for c in changes}
    assert paths == {"CODEOWNERS", ".github/workflows/ci.yml", ".codeatlas/policy.yaml"}


def test_governance_changes_empty_when_no_governance_touched() -> None:
    policy = _policy(["CODEOWNERS"])
    changed = [("src/foo.py", "modified"), ("README.md", "modified")]
    changes = governance_changes_in_diff(
        changed_files=changed, policy=policy, retrieved_at=NOW,
    )
    assert changes == []


# ---- Property test for CODEOWNERS last-match semantics ---------------------

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    _slug = st.text(alphabet="abcdefghij", min_size=1, max_size=4)
    _owner = st.builds(lambda s: f"@user-{s}", _slug)

    @settings(max_examples=200)
    @given(
        rules=st.lists(
            st.tuples(st.sampled_from(["*", "/*.py", "/*.md", "/foo/", "/foo/bar.py"]),
                      st.lists(_owner, min_size=1, max_size=2)),
            min_size=1, max_size=6,
        ),
        path=st.sampled_from(["foo/bar.py", "a.py", "a.md", "nested/x.md"]),
    )
    def test_property_last_match_wins_matches_naive(rules, path) -> None:
        """Our last-match impl equals a naive 'iterate; keep updating' reference."""
        text = "\n".join(f"{p} {' '.join(os)}" for p, os in rules)
        parsed = parse_codeowners(text)
        ours = owners_for(path, parsed)

        reference: list[str] = []
        for rule in parsed:
            from codeatlas.analyze.governance import _codeowners_match

            if _codeowners_match(rule.pattern, path):
                reference = list(rule.owners)
        assert ours == reference
except ImportError:
    pytest.skip("hypothesis not installed", allow_module_level=False)
