import typer

app = typer.Typer(
    name="magpie",
    help="Tag JPEG/HEIC photos with IPTC captions and keywords via a local vision LLM.",
    no_args_is_help=True,
)


@app.command()
def tag(path: str) -> None:
    """Tag a file or folder (recursive) and exit when done."""
    typer.echo(f"tag: not yet implemented ({path})")


@app.command()
def watch(paths: list[str]) -> None:
    """Watch folder(s) and tag new JPEGs as they appear."""
    typer.echo(f"watch: not yet implemented ({paths})")


@app.command()
def config() -> None:
    """Open $EDITOR on the config file."""
    typer.echo("config: not yet implemented")
