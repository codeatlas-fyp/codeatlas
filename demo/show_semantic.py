"""Terminal demo: criteria splitting + semantic candidate ranking + link states.

Uses hardcoded tiny inputs so you can see the pipeline without needing a real
PR. Pass `--download` to actually fetch and use the embedding model (first
run downloads ~90 MB and takes ~30 seconds; subsequent runs are instant).
Default is BM25-only so the demo works offline.

Usage:
    uv run python -m demo.show_semantic
    uv run python -m demo.show_semantic --download   # include dense model
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeatlas.analyze.criteria import split_criteria
from codeatlas.analyze.semantic import rank_candidates
from codeatlas.schema import CodeEntity

CONSOLE = Console()

SAMPLE_DESCRIPTION = """\
As a librarian I want late fees so overdue books come back.

Acceptance Criteria:
- Late fees are shown rounded to two decimal places
- Daily rate is capped at $0.25
- First-time offenders can waive fees under one week
"""

SAMPLE_ENTITIES = [
    CodeEntity(
        entity_id="pkg.payments.fees.calculate_late_fee",
        kind="function",
        file="pkg/payments/fees.py",
        start_line=10, end_line=18,
        signature="def calculate_late_fee(days_overdue: int) -> Decimal",
        docstring="Return the late fee for days_overdue days, capped at MAX_LATE_FEE.",
    ),
    CodeEntity(
        entity_id="pkg.payments.fees.is_fee_waivable",
        kind="function",
        file="pkg/payments/fees.py",
        start_line=21, end_line=24,
        signature="def is_fee_waivable(days_overdue: int, is_first_offence: bool) -> bool",
        docstring="First-offenders can waive fees under one week.",
    ),
    CodeEntity(
        entity_id="pkg.payments.fees.format_receipt",
        kind="function",
        file="pkg/payments/fees.py",
        start_line=27, end_line=29,
        signature="def format_receipt(borrower_name: str, book_title: str, fee: Decimal) -> str",
        docstring="Format a receipt string for a late fee charge.",
    ),
    CodeEntity(
        entity_id="pkg.books.Catalog.add",
        kind="method",
        file="pkg/books.py",
        start_line=30, end_line=35,
        signature="def add(self, book: Book) -> None",
        docstring="Add a book to the catalog.",
    ),
    CodeEntity(
        entity_id="pkg.loans.Loan.mark_returned",
        kind="method",
        file="pkg/loans.py",
        start_line=20, end_line=22,
        signature="def mark_returned(self) -> None",
        docstring="Flag the loan as returned.",
    ),
]


def _dummy_banner(what: str) -> None:
    CONSOLE.print(Panel.fit(
        f"[yellow][DUMMY — hardcoded for demo][/yellow]\n{what}",
        border_style="yellow",
    ))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--download", action="store_true",
                   help="Use dense embeddings (downloads sentence-transformers model).")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    CONSOLE.print(Panel.fit("[bold]Phase 1: split the acceptance criteria[/bold]", border_style="cyan"))
    _dummy_banner("Using a hardcoded SBX-4 requirement description.")
    criteria = split_criteria(requirement_key="SBX-4", version_no=1, description=SAMPLE_DESCRIPTION)
    t = Table(title=f"Criteria ({len(criteria)})")
    t.add_column("id", style="cyan"); t.add_column("text", overflow="fold"); t.add_column("auto?")
    for c in criteria:
        t.add_row(c.criterion_id, c.text, "yes" if c.derived_from_description else "no")
    CONSOLE.print(t)

    CONSOLE.print(Panel.fit("[bold]Phase 2: rank entities per criterion (BM25 + RRF)[/bold]", border_style="cyan"))
    _dummy_banner(
        f"Using {len(SAMPLE_ENTITIES)} hardcoded entities.\n"
        f"Dense model: {'ENABLED (will download)' if args.download else 'disabled (BM25-only)'}"
    )
    result = rank_candidates(
        criteria=criteria, entities=SAMPLE_ENTITIES, top_k=5, bm25_only=not args.download,
    )
    CONSOLE.print(f"[dim]model_id captured for bundle: {result.model_id}[/dim]\n")

    by_criterion: dict[str, list] = {}
    for cand in result.candidates:
        by_criterion.setdefault(cand.criterion_id, []).append(cand)

    for crit in criteria:
        t = Table(title=f"Top candidates for {crit.criterion_id}  —  {crit.text[:60]}")
        t.add_column("rank", style="bold"); t.add_column("entity_id", style="cyan")
        t.add_column("bm25"); t.add_column("dense"); t.add_column("fused score")
        for cand in by_criterion.get(crit.criterion_id, []):
            t.add_row(
                str(cand.fused_rank), cand.entity_id,
                str(cand.bm25_rank) if cand.bm25_rank else "—",
                str(cand.dense_rank) if cand.dense_rank else "—",
                f"{cand.fused_score:.4f}",
            )
        CONSOLE.print(t)

    _dummy_banner(
        "Link states (OBSERVED, DETERMINISTICALLY_DERIVED, SEMANTIC_CANDIDATE, UNRESOLVED,\n"
        "CONFLICTING) are assigned once all evidence is collected in a real bundle.\n"
        "See evaluate/links.py and the test_links.py cases for the full logic."
    )


if __name__ == "__main__":
    main()
