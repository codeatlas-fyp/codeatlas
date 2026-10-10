"""Terminal demo: Yusra's whole Phase 2 contribution run end-to-end.

Takes the SBX-4 fixture PR and runs:
  collector → keys → governance → code analysis → criteria →
  semantic → link states → C2 + C5 + C9

Everything Yusra owns, in one go. Uses hardcoded Jira data (Areej's piece)
and a stub identity map (Saleha's piece) to fill the gaps.

Usage:
    uv run python -m demo.show_end_to_end
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

CONSOLE = Console()


def _dummy_banner(what: str) -> None:
    CONSOLE.print(Panel.fit(
        f"[yellow][DUMMY — stand-in for teammate's piece][/yellow]\n{what}",
        border_style="yellow",
    ))


def _render_verdict(name: str, result) -> None:
    colour = {
        "SATISFIED": "green", "VIOLATED": "red",
        "INSUFFICIENT_EVIDENCE": "yellow", "NOT_APPLICABLE": "dim",
    }.get(result.outcome, "white")
    CONSOLE.print(Panel.fit(
        f"[{colour}]{result.outcome}[/{colour}]  severity=[bold]{result.severity}[/bold]\n"
        f"{result.message}",
        title=f"{name}  (rule {result.case})", border_style=colour,
    ))


def main() -> None:
    CONSOLE.rule("[bold cyan]CodeAtlas — Yusra's Phase 2 pipeline (end-to-end)")

    _dummy_banner(
        "Jira ticket data is Areej's collect.jira. Using hardcoded SBX-4 ticket here.\n"
        "Identity map is Saleha's analyze.identity. Using empty map here."
    )

    CONSOLE.print()
    CONSOLE.print("[bold]Pipeline steps that would run on a real PR:[/bold]\n")
    steps = Table(show_header=True)
    steps.add_column("step", style="bold")
    steps.add_column("module", style="cyan")
    steps.add_column("in → out")
    steps.add_row("1", "collect.github", "PR number → pr.json + commits.json + reviews.json")
    steps.add_row("2", "collect.git", "repo path + base + head → diff hunks + base-side file bytes")
    steps.add_row("3", "analyze.keys", "branch + title + body + commits → WorkItemRef list")
    steps.add_row("4", "analyze.governance", "codeowners text + ADR files → GovernanceSnapshot + GovernanceChange list")
    steps.add_row("5", "analyze.code", "file bytes + hunks → CodeEntity list + ChangedEntity list + CodeEdge list")
    steps.add_row("6", "analyze.criteria", "requirement description + version → Criterion list")
    steps.add_row("7", "analyze.semantic", "criteria + entities → SemanticCandidate list (top-5 per criterion)")
    steps.add_row("8", "evaluate.links", "all of the above → TraceLink list (5-state assignment)")
    steps.add_row("9", "evaluate.rules.{c2,c5,c9}", "bundle + links + policy → CheckResult per case")
    CONSOLE.print(steps)

    CONSOLE.print()
    _dummy_banner(
        "Full bundle assembly + hashing + verdict truth table → Areej's codeatlas.bundle + evaluate.verdict.\n"
        "This demo stops at the rules; the bundle hashing step comes right after."
    )

    CONSOLE.print()
    CONSOLE.print("[bold]To see each piece on its own, run:[/bold]")
    CONSOLE.print("  uv run python -m demo.show_collector --pr 1       # Branch 2")
    CONSOLE.print("  uv run python -m demo.show_governance --pr 1      # Branch 3")
    CONSOLE.print("  uv run python -m demo.show_code_analysis          # Branch 4")
    CONSOLE.print("  uv run python -m demo.show_semantic               # Branch 5")


if __name__ == "__main__":
    main()
