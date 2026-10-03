import typer

from codeatlas import __version__

app = typer.Typer(help="CodeAtlas command-line interface.")


@app.callback()
def main() -> None:
    """CodeAtlas command-line interface."""


@app.command()
def version() -> None:
    """Print the CodeAtlas version."""
    typer.echo(__version__)
