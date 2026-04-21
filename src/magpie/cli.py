"""Typer CLI: `magpie tag`, `magpie watch`, `magpie config`."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from magpie.config import DEFAULT_CONFIG_PATH, Config, ConfigError
from magpie.runner import BatchRunner, default_csv_path
from magpie.tagger import Tagger
from magpie.watcher import Watcher
from magpie.writer import MetadataWriter

app = typer.Typer(
    name="magpie",
    help="Tag JPEG/HEIC photos with IPTC captions and keywords via a local vision LLM.",
    no_args_is_help=True,
)


def _load_config(config_path: Path | None) -> Config:
    try:
        return Config.load(config_path)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _check_exiftool() -> None:
    if shutil.which("exiftool") is None:
        typer.echo(
            "error: exiftool binary not found. Install with `brew install exiftool`.",
            err=True,
        )
        raise typer.Exit(code=2)


@app.command()
def tag(
    path: Annotated[Path, typer.Argument(help="File or folder to tag (recursive).")],
    hint: Annotated[str, typer.Option(help="Optional context hint for the model.")] = "",
    force: Annotated[bool, typer.Option(help="Skip the already-tagged check.")] = False,
    endpoint: Annotated[
        str | None,
        typer.Option(help="Endpoint name from config (overrides MAGPIE_ENDPOINT)."),
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to config.toml")
    ] = None,
) -> None:
    """Tag a file or folder and exit when done."""
    _check_exiftool()
    cfg = _load_config(config_path)
    ep = cfg.endpoint(endpoint)
    tagger = Tagger(endpoint=ep, prompt=cfg.prompt, max_keywords=cfg.max_keywords)
    with MetadataWriter() as writer:
        csv_path = default_csv_path()
        runner = BatchRunner(
            tagger=tagger,
            writer=writer,
            model_id=ep.model,
            concurrency=cfg.concurrency,
            csv_path=csv_path,
            hint=hint,
        )
        summary = asyncio.run(runner.run([path], force=force))

    typer.echo(
        f"tagged={summary.tagged} skipped={summary.skipped} "
        f"failed={summary.failed} csv={csv_path}"
    )
    if summary.failed:
        raise typer.Exit(code=1)


@app.command()
def watch(
    paths: Annotated[list[Path], typer.Argument(help="Folder(s) to watch.")],
    hint: Annotated[str, typer.Option(help="Optional context hint.")] = "",
    endpoint: Annotated[str | None, typer.Option(help="Endpoint name.")] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to config.toml")
    ] = None,
) -> None:
    """Watch folder(s) and tag new JPEGs as they appear."""
    _check_exiftool()
    cfg = _load_config(config_path)
    ep = cfg.endpoint(endpoint)

    async def run() -> None:
        tagger = Tagger(endpoint=ep, prompt=cfg.prompt, max_keywords=cfg.max_keywords)
        with MetadataWriter() as writer:
            csv_path = default_csv_path()
            runner = BatchRunner(
                tagger=tagger,
                writer=writer,
                model_id=ep.model,
                concurrency=cfg.concurrency,
                csv_path=csv_path,
                hint=hint,
            )

            async def process(path: Path) -> None:
                summary = await runner.run([path])
                # Raise so the watcher's exponential-backoff loop retries
                # transient endpoint or exiftool failures.
                if summary.failed:
                    raise RuntimeError(f"tagging failed for {path}")

            watcher = Watcher(paths=paths, process=process)
            await watcher.start()
            stop = asyncio.Event()
            try:
                await stop.wait()
            except (asyncio.CancelledError, KeyboardInterrupt):
                pass
            finally:
                await watcher.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        typer.echo("shutting down", err=True)


@app.command("ui")
def ui_cmd(
    port: Annotated[int, typer.Option(help="HTTP port (auto-retries if busy).")] = 7799,
    host: Annotated[str, typer.Option(help="Host to bind.")] = "127.0.0.1",
    open_browser: Annotated[
        bool,
        typer.Option("--open/--no-open", help="Open the UI in a browser."),
    ] = True,
) -> None:
    """Serve the local web dashboard — a cabinet of captioned things."""
    from magpie.webui import serve

    typer.echo(f"magpie · starting ui at http://{host}:{port}/")
    serve(host=host, port=port, open_browser=open_browser)


@app.command("config")
def config_cmd(
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to config.toml")
    ] = None,
) -> None:
    """Open $EDITOR on the config file (creates it from the default template if absent)."""
    path = config_path or DEFAULT_CONFIG_PATH
    # Ensure file exists and is valid by attempting to load
    _load_config(path)
    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(path)], check=False)
    except FileNotFoundError:
        typer.echo(f"editor '{editor}' not found; config at {path}", err=True)
        sys.exit(2)
