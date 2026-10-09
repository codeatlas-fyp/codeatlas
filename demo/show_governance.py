"""Terminal demo: parse CODEOWNERS, build governance snapshot, run C2 + C5.

Uses the SBX-5 (CODEOWNERS edit) fixture from Branch 2 so you can see C2 fire.
Also runs against the SBX-4 (PASS) fixture to show C2 and C5 both satisfied.

Usage:
    uv run python -m demo.show_governance --pr 1    # SBX-4 PASS
    uv run python -m demo.show_governance --pr 2    # SBX-5 C2 violated
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeatlas.analyze.governance import (
    build_snapshot,
    governance_changes_in_diff,
    owners_for,
)
from codeatlas.evaluate.rules.c2_pr_modifies_governance import C2PrModifiesGovernance
from codeatlas.evaluate.rules.c5_owner_did_not_approve import C5OwnerDidNotApprove
from codeatlas.schema import (
    ApprovalPolicy,
    AuthorityPolicy,
    ChangedEntity,
    ChangeRef,
    Commit,
    EvidenceBundle,
    IdentityMap,
    Policy,
    Review,
    SemanticPolicy,
)

CONSOLE = Console()
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "github"


def _dummy_banner(what: str) -> None:
    CONSOLE.print(
        Panel.fit(
            f"[yellow][DUMMY — not Yusra's part][/yellow]\n{what}",
            border_style="yellow",
        )
    )


def _policy() -> Policy:
    return Policy(
        version=1,
        workitem_key_pattern=r"[A-Z]+-\d+",
        requirement_fields=["summary", "description", "acceptance_criteria"],
        governance_paths=[
            "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
            "docs/adr/**", ".codeatlas/**", ".github/workflows/**",
            ".pre-commit-config.yaml",
        ],
        approval=ApprovalPolicy(status="Approved", approver_account_ids=["acct-1"]),
        priority_authority=AuthorityPolicy(account_ids=["acct-1"]),
        cases={},
        semantic=SemanticPolicy(top_k=5),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Governance snapshot + C2/C5 demo.")
    parser.add_argument("--pr", type=int, default=1)
    parser.add_argument(
        "--repo", default="codeatlas-fyp/codeatlas-sandbox",
        help="Only used to find the fixture folder.",
    )
    return parser.parse_args()


def _load_fixture(repo: str, pr: int) -> tuple[dict, list[dict], list[dict], str | None]:
    import json
    folder = FIXTURES / repo.replace("/", "__") / f"pr-{pr}"
    pr_data = json.loads((folder / "pr.json").read_text())
    commits = json.loads((folder / "commits.json").read_text())
    reviews = json.loads((folder / "reviews.json").read_text())
    codeowners = (folder / "codeowners.txt").read_text() if (folder / "codeowners.txt").is_file() else None
    return pr_data, commits, reviews, codeowners


def _render_codeowners(text: str | None) -> None:
    if text is None:
        CONSOLE.print("[red]No CODEOWNERS at base commit.[/red]")
        return
    table = Table(title="CODEOWNERS rules (parsed, in file order)")
    table.add_column("line", style="dim", width=4)
    table.add_column("pattern", style="bold")
    table.add_column("owners")
    from codeatlas.analyze.governance import parse_codeowners
    for rule in parse_codeowners(text):
        table.add_row(str(rule.line), rule.pattern, " ".join(rule.owners))
    CONSOLE.print(table)


def _render_resolved_ownership(codeowners_text: str | None, paths: list[str]) -> None:
    if codeowners_text is None:
        return
    from codeatlas.analyze.governance import parse_codeowners
    rules = parse_codeowners(codeowners_text)
    table = Table(title=f"Ownership for {len(paths)} changed path(s) (last-match wins)")
    table.add_column("path", style="cyan")
    table.add_column("owner(s)", style="bold")
    for path in paths:
        resolved = owners_for(path, rules)
        table.add_row(path, " ".join(resolved) if resolved else "[dim]no owner[/dim]")
    CONSOLE.print(table)


def _render_result(result) -> None:
    colour = {
        "SATISFIED": "green",
        "VIOLATED": "red",
        "INSUFFICIENT_EVIDENCE": "yellow",
        "NOT_APPLICABLE": "dim",
    }.get(result.outcome, "white")
    panel = Panel.fit(
        f"[{colour}]{result.outcome}[/{colour}]  severity=[bold]{result.severity}[/bold]\n"
        f"{result.message}\n"
        f"[dim]evidence: {', '.join(result.evidence_ids[:3])}"
        + (f" (+{len(result.evidence_ids) - 3} more)" if len(result.evidence_ids) > 3 else "")
        + "[/dim]",
        title=f"Rule {result.case}", border_style=colour,
    )
    CONSOLE.print(panel)


def main() -> None:
    args = _parse_args()
    pr_data, commits_data, reviews_data, codeowners = _load_fixture(args.repo, args.pr)

    CONSOLE.print(Panel.fit(
        f"[bold]PR #{pr_data['number']}[/bold] — {pr_data['title']}",
        border_style="cyan",
    ))

    _render_codeowners(codeowners)

    # Simulate a diff that touches these paths. In real pipeline this comes
    # from Branch 2's LocalRepo.changed_paths().
    simulated_changed_paths = {
        1: [("payments/fees.py", "modified")],                 # SBX-4 PASS
        2: [("CODEOWNERS", "modified"), ("loans.py", "modified")],  # SBX-5 C2
    }.get(args.pr, [("README.md", "modified")])

    _dummy_banner(
        f"Simulating a diff of {len(simulated_changed_paths)} file(s). "
        "In the real pipeline, Yusra's LocalRepo.changed_paths() produces this list."
    )

    _render_resolved_ownership(codeowners, [p for p, _ in simulated_changed_paths])

    # Build governance snapshot + changes.
    now = datetime.now(UTC)
    snapshot = build_snapshot(
        base_sha=pr_data["base"]["sha"],
        codeowners_text=codeowners,
        adr_files={},
        policy_blob_sha=None,
        retrieved_at=now,
    )
    policy = _policy()
    gov_changes = governance_changes_in_diff(
        changed_files=simulated_changed_paths, policy=policy, retrieved_at=now,
    )

    # Build a minimal bundle for the rules.
    bundle_commits = [
        Commit(
            evidence_id=f"commit:{c['sha']}", source="github",
            source_ref=f"commit:{c['sha']}", retrieved_at=now,
            extractor_version="github@0.1.0",
            sha=c["sha"], author_email=((c.get("commit") or {}).get("author") or {}).get("email", "x@example.test"),
            author_login=(c.get("author") or {}).get("login"),
            committed_at=now, message=((c.get("commit") or {}).get("message") or ""),
        )
        for c in commits_data
    ]
    bundle_reviews = [
        Review(
            evidence_id=f"review:{r['user']['login']}:{r['commit_id']}", source="github",
            source_ref="review", retrieved_at=now, extractor_version="github@0.1.0",
            reviewer_login=r["user"]["login"], state=r["state"],
            commit_id=r["commit_id"],
            submitted_at=datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")),
        )
        for r in reviews_data
    ]
    bundle_changed = [
        ChangedEntity(
            evidence_id=f"changed:{path}", source="analysis", source_ref=f"diff:{path}",
            retrieved_at=now, extractor_version="code@0.1.0",
            entity_id=None, change=ct,  # type: ignore[arg-type]
            file=path, hunk_lines=(1, 10), commit_shas=[pr_data["head"]["sha"]],
        )
        for path, ct in simulated_changed_paths
    ]

    bundle = EvidenceBundle(
        schema_version="0",
        change=ChangeRef(
            repo=args.repo, pr_number=args.pr,
            base_sha=pr_data["base"]["sha"], head_sha=pr_data["head"]["sha"],
            merge_base_sha=pr_data["base"]["sha"],
        ),
        codeatlas_version="0.1.0", collected_at=now, run_id="demo",
        extractor_versions={"github": "0.1.0", "governance": "0.1.0"},
        model_ids=[], model_file_hashes={},
        policy=policy, identity_map=IdentityMap(people=[]),
        work_items=[], change_events=[], versions=[], lifecycle=[],
        commits=bundle_commits, reviews=bundle_reviews, people=[],
        governance=snapshot, governance_changes=gov_changes,
        entities=[], changed=bundle_changed, edges=[],
        criteria=[], candidates=[],
        collection_notes=[],
    )

    CONSOLE.print()
    for rule in (C2PrModifiesGovernance(), C5OwnerDidNotApprove()):
        _render_result(rule.check(bundle, [], bundle.policy))

    _dummy_banner(
        "Bundle hashing, verdict truth table, replay — Areej's codeatlas.bundle & evaluate.verdict\n"
        "Identity map team expansion — Saleha's codeatlas.analyze.identity"
    )


if __name__ == "__main__":
    main()
