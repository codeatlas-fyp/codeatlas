"""Terminal demo: parse a Python file, show its entities, map hunks.

Uses the mini_sandbox fixture repo so you can see entity extraction and hunk
mapping without needing a real cloned repo. Also shows the import graph.

Usage:
    uv run python -m demo.show_code_analysis
    uv run python -m demo.show_code_analysis --file payments/fees.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codeatlas.analyze.code import (
    build_import_graph,
    extract_entities,
    map_hunks_to_entities,
)

CONSOLE = Console()
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "repos" / "mini_sandbox"


def _dummy_banner(what: str) -> None:
    CONSOLE.print(Panel.fit(
        f"[yellow][DUMMY — not Yusra's part][/yellow]\n{what}",
        border_style="yellow",
    ))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Code analysis demo.")
    parser.add_argument("--file", default="payments/fees.py")
    return parser.parse_args()


def _render_entities(file_path: str, entities: list) -> None:
    table = Table(title=f"Entities in {file_path} ({len(entities)})")
    table.add_column("entity_id", style="cyan", overflow="fold")
    table.add_column("kind", style="bold")
    table.add_column("lines")
    table.add_column("docstring", overflow="fold")
    for entity in entities:
        table.add_row(
            entity.entity_id, entity.kind,
            f"{entity.start_line}-{entity.end_line}",
            (entity.docstring or "")[:60],
        )
    CONSOLE.print(table)


def _render_mapping(file_path: str, changed: list) -> None:
    table = Table(title=f"Hunk → entity mapping ({len(changed)} hunks)")
    table.add_column("hunk lines", style="cyan")
    table.add_column("entity_id", style="bold")
    table.add_column("change")
    for ce in changed:
        entity_display = ce.entity_id if ce.entity_id else "[dim](unresolved)[/dim]"
        colour = "green" if ce.entity_id else "yellow"
        table.add_row(
            f"{ce.hunk_lines[0]}-{ce.hunk_lines[1]}",
            f"[{colour}]{entity_display}[/{colour}]",
            ce.change,
        )
    CONSOLE.print(table)


def _render_imports(edges: list) -> None:
    table = Table(title=f"Import graph ({len(edges)} edges)")
    table.add_column("from", style="cyan")
    table.add_column("imports", style="bold")
    for edge in edges:
        if edge.kind == "IMPORTS":
            table.add_row(edge.src, edge.dst)
    CONSOLE.print(table)


def main() -> None:
    args = _parse_args()
    target = FIXTURE / args.file
    if not target.is_file():
        CONSOLE.print(f"[red]No fixture file at {target}[/red]")
        CONSOLE.print("[dim]Available:[/dim]")
        for f in FIXTURE.rglob("*.py"):
            CONSOLE.print(f"  {f.relative_to(FIXTURE)}")
        return

    source = target.read_bytes()
    file_path = str(target.relative_to(FIXTURE))

    CONSOLE.print(Panel.fit(
        f"[bold]Analysing {file_path}[/bold] ({len(source)} bytes)",
        border_style="cyan",
    ))

    entities = extract_entities(file_path=file_path, source=source)
    _render_entities(file_path, entities)

    non_module = [e for e in entities if e.kind != "module"]
    if non_module:
        first = non_module[0]
        sim_hunks = [(first.start_line, first.end_line), (2, 3)]
    else:
        sim_hunks = [(1, 5)]
    _dummy_banner(
        f"Simulating {len(sim_hunks)} diff hunk(s). "
        "In production, Branch 2's LocalRepo.diff_hunks() provides these."
    )
    changed = map_hunks_to_entities(
        file_path=file_path, entities=entities,
        hunk_lines=sim_hunks, change_type="modified",
        commit_shas=["abc123"], retrieved_at=datetime.now(UTC),
    )
    _render_mapping(file_path, changed)

    all_files = {str(f.relative_to(FIXTURE)): f.read_bytes() for f in FIXTURE.rglob("*.py")}
    all_entities = []
    for fp, src in all_files.items():
        all_entities.extend(extract_entities(file_path=fp, source=src))
    edges = build_import_graph(entities=all_entities, file_sources=all_files)
    _render_imports(edges)

    _dummy_banner(
        "CALLS edges (function → function) are deferred to Iteration 2 per spec D-table D10.\n"
        "Full bundle assembly and verdict → Areej's codeatlas.bundle and evaluate.verdict."
    )


if __name__ == "__main__":
    main()
