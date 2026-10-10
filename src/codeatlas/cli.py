"""`codeatlas` command line (spec §7.7 as decided in the step-8 spec).

Exit codes (spec §5): 0 PASS, 1 FAIL, 2 REVIEW or UNKNOWN, 3 collection error, 4 invalid
evidence or config, 5 replay mismatch.
"""

import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from codeatlas import __version__
from codeatlas.bundle.replay import replay as replay_bundle
from codeatlas.bundle.store import BundleStore, bundle_folder, load_bundle, load_verdict
from codeatlas.collect.jira.client import JiraClient
from codeatlas.collect.jira.recorder import FixtureJiraSource
from codeatlas.config import Settings, load_change, load_identity_map, load_policy
from codeatlas.errors import CodeAtlasError
from codeatlas.logging import EventLog
from codeatlas.pipeline import (
    CollectRequest,
    JiraSource,
    collect_and_evaluate,
    evaluate_bundle,
    now_utc,
)
from codeatlas.schema import Verdict

EXIT_CODES = {"PASS": 0, "FAIL": 1, "REVIEW": 2, "UNKNOWN": 2}

app = typer.Typer(help="CodeAtlas: evaluate a change against the requirement it implements.")


@app.callback()
def main() -> None:
    """CodeAtlas command-line interface."""


def _fail(log: EventLog, error: CodeAtlasError) -> typer.Exit:
    log.event("error", level="ERROR", error=type(error).__name__, message=str(error))
    typer.echo(f"error ({type(error).__name__}): {error}", err=True)
    return typer.Exit(error.exit_code)


def _print_verdict(bundle_hash: str, verdict: Verdict) -> None:
    typer.echo(f"bundle  {bundle_hash}")
    typer.echo(f"verdict {verdict.value}  (verdict_hash {verdict.verdict_hash})")
    for result in verdict.results:
        typer.echo(f"  {result.case}  {result.outcome:<22} {result.severity:<6} {result.message}")


@app.command()
def collect(
    change: Annotated[Path, typer.Option(help="Change file standing in for GitHub/git evidence.")],
    policy: Annotated[Path | None, typer.Option(help="Policy YAML (default: built-in).")] = None,
    identity_map: Annotated[Path | None, typer.Option(help="Identity map YAML.")] = None,
    fixtures: Annotated[
        Path | None, typer.Option(help="Read recorded Jira responses instead of live Jira.")
    ] = None,
    store: Annotated[Path | None, typer.Option(help="Bundle store folder.")] = None,
) -> None:
    """Collect requirement evidence, freeze it, evaluate it and store bundle and verdict."""
    settings = Settings()
    log = EventLog(uuid.uuid4().hex, level=settings.log_level)
    try:
        loaded_policy, notes = load_policy(policy)
        request = CollectRequest(
            change=load_change(change),
            change_label=change.name,
            policy=loaded_policy,
            identity_map=load_identity_map(identity_map),
            run_id=log.run_id,
            collected_at=now_utc(),
            notes=notes,
        )
        source: JiraSource
        if fixtures is not None:
            source = FixtureJiraSource(fixtures)
        else:
            base_url, email, token = settings.jira()
            source = JiraClient(base_url, email, token, max_retries=settings.jira_max_retries)
        bundle_hash, verdict = collect_and_evaluate(
            request, source, BundleStore(store or settings.bundle_dir), log
        )
    except CodeAtlasError as error:
        raise _fail(log, error) from error
    _print_verdict(bundle_hash, verdict)
    raise typer.Exit(EXIT_CODES[verdict.value])


@app.command()
def evaluate(
    bundle: Annotated[Path, typer.Option(help="Stored bundle folder or its bundle.json.")],
) -> None:
    """Evaluate a stored bundle offline and store its verdict."""
    log = EventLog(uuid.uuid4().hex)
    folder = bundle_folder(bundle)
    try:
        frozen = load_bundle(folder)
        verdict = evaluate_bundle(frozen, folder.name)
        BundleStore(folder.parent).save_verdict(verdict)
    except CodeAtlasError as error:
        raise _fail(log, error) from error
    _print_verdict(folder.name, verdict)
    raise typer.Exit(EXIT_CODES[verdict.value])


@app.command()
def replay(path: Path) -> None:
    """Recompute a stored verdict and prove it is identical (exit 5 if not)."""
    log = EventLog(uuid.uuid4().hex)
    try:
        result = replay_bundle(path, evaluate_bundle)
    except CodeAtlasError as error:
        raise _fail(log, error) from error
    typer.echo(f"bundle      {result.bundle_hash}")
    typer.echo(f"stored      {result.stored_verdict_hash}")
    typer.echo(f"recomputed  {result.recomputed_verdict_hash}")
    typer.echo("match       yes")


@app.command()
def show(
    path: Path,
    as_json: Annotated[bool, typer.Option("--json", help="Print the stored verdict JSON.")] = False,
) -> None:
    """Print a stored bundle's verdict and case results."""
    log = EventLog(uuid.uuid4().hex)
    folder = bundle_folder(path)
    try:
        load_bundle(folder)
        verdict = load_verdict(folder)
    except CodeAtlasError as error:
        raise _fail(log, error) from error
    if as_json:
        typer.echo(json.dumps(verdict.model_dump(mode="json"), indent=2))
    else:
        _print_verdict(folder.name, verdict)


@app.command()
def version() -> None:
    """Print the CodeAtlas version."""
    typer.echo(__version__)
