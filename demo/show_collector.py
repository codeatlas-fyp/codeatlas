"""Terminal demo: run the GitHub collector + key finder on a recorded PR.

No web UI, no graph rendering, no Jira. Just Rich tables so you can see what
the collector produced. Use `--live` to hit real GitHub (needs
CODEATLAS_GITHUB_TOKEN); default uses the committed fixtures.

Usage:
    uv run python -m demo.show_collector --pr 1
    uv run python -m demo.show_collector --pr 2
    uv run python -m demo.show_collector --repo codeatlas-fyp/codeatlas-sandbox --pr 1 --live
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeatlas.analyze.keys import find_keys
from codeatlas.collect.github import (
    FixtureSource,
    GitHubClient,
    GitHubClientConfig,
    client_from_env,
)

CONSOLE = Console()
DEFAULT_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "github"


def _dummy_banner(what: str) -> None:
    CONSOLE.print(
        Panel.fit(
            f"[yellow][DUMMY — this piece is NOT Yusra's code][/yellow]\n{what}",
            border_style="yellow",
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show GitHub collector + key finder output.")
    parser.add_argument("--repo", default="codeatlas-fyp/codeatlas-sandbox")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--live", action="store_true",
                        help="Hit real GitHub (requires CODEATLAS_GITHUB_TOKEN).")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    return parser.parse_args()


def _source(args: argparse.Namespace) -> object:
    if args.live:
        CONSOLE.print("[green]Mode: LIVE — hitting api.github.com[/green]")
        return client_from_env()
    CONSOLE.print(f"[cyan]Mode: FIXTURES — reading {args.fixtures}[/cyan]")
    return FixtureSource(root=args.fixtures)


def _render_pr(pr: dict) -> None:
    base_sha = (pr.get("base") or {}).get("sha", "?")
    head_sha = (pr.get("head") or {}).get("sha", "?")
    table = Table(title=f"PR #{pr.get('number')} — {pr.get('title', '?')}", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("state", str(pr.get("state", "?")))
    table.add_row("author", str((pr.get("user") or {}).get("login", "?")))
    table.add_row("base", f"{(pr.get('base') or {}).get('ref', '?')} @ {base_sha[:12]}")
    table.add_row("head", f"{(pr.get('head') or {}).get('ref', '?')} @ {head_sha[:12]}")
    table.add_row("body", (pr.get("body") or "")[:120])
    CONSOLE.print(table)


def _render_commits(commits: list[dict]) -> None:
    table = Table(title=f"Commits ({len(commits)})")
    table.add_column("sha", style="cyan")
    table.add_column("author")
    table.add_column("message", overflow="fold")
    for commit in commits:
        sha = commit.get("sha", "?")[:12]
        author_login = (commit.get("author") or {}).get("login", "?")
        message = ((commit.get("commit") or {}).get("message") or "").splitlines()[0]
        table.add_row(sha, author_login, message)
    CONSOLE.print(table)


def _render_reviews(reviews: list[dict]) -> None:
    table = Table(title=f"Reviews ({len(reviews)})")
    table.add_column("reviewer")
    table.add_column("state", style="bold")
    table.add_column("commit_id", style="cyan")
    table.add_column("submitted_at")
    if not reviews:
        table.add_row("—", "no reviews", "—", "—")
    for review in reviews:
        state_colour = {"APPROVED": "green", "CHANGES_REQUESTED": "red"}.get(review.get("state", ""), "white")
        table.add_row(
            (review.get("user") or {}).get("login", "?"),
            f"[{state_colour}]{review.get('state', '?')}[/{state_colour}]",
            str(review.get("commit_id", "?"))[:12],
            str(review.get("submitted_at", "?")),
        )
    CONSOLE.print(table)


def _render_keys(refs: list) -> None:
    table = Table(title=f"Work-item keys found ({len(refs)})")
    table.add_column("key", style="bold")
    table.add_column("found_in")
    table.add_column("commit_sha", style="cyan")
    for ref in refs:
        table.add_row(ref.key, ref.found_in, (ref.commit_sha or "—")[:12])
    CONSOLE.print(table)


def main() -> None:
    args = _parse_args()
    source = _source(args)

    pr = source.pull_request(args.repo, args.pr)
    commits = source.commits(args.repo, args.pr)
    reviews = source.reviews(args.repo, args.pr)

    _render_pr(pr)
    _render_commits(commits)
    _render_reviews(reviews)

    refs = find_keys(
        branch=(pr.get("head") or {}).get("ref", ""),
        pr_title=pr.get("title", ""),
        pr_body=pr.get("body", ""),
        commits=commits,
        retrieved_at=datetime.now(UTC),
    )
    _render_keys(refs)

    _dummy_banner(
        "The following come from Areej / Saleha and are not shown here:\n"
        "  • Jira ticket data (change history, approvals) — Areej's collect.jira\n"
        "  • EvidenceBundle assembly + hashing — Areej's bundle package\n"
        "  • Identity map (git_email → github_login → jira_account_id) — Saleha's analyze.identity\n"
    )


if __name__ == "__main__":
    main()
