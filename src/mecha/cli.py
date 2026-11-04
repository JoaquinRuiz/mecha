"""Command line interface (Typer). ``mecha`` with no subcommand runs ``serve``."""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from mecha import __version__
from mecha.server import create_app, generate_token

HOST = "127.0.0.1"
DEFAULT_PORT = 7878
VITE_DEV_URL = "http://localhost:5173"

# Exit codes shared by every command (see SPEC.md, "CLI").
EXIT_OK = 0
EXIT_AMBER = 1
EXIT_RED = 2
EXIT_FAILURE = 3

console = Console(stderr=True)
app = typer.Typer(
    name="mecha",
    help="How much fuse is left on your API keys?",
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_show_locals=False,  # never dump locals: they may hold secrets
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mecha {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    _version_flag: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is None:
        serve()


def _open_when_ready(server: uvicorn.Server, url: str) -> None:
    for _ in range(200):
        if server.started:
            webbrowser.open(url)
            return
        if server.should_exit:
            return
        time.sleep(0.05)


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port to listen on (127.0.0.1 only).")] = DEFAULT_PORT,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open the browser.")] = False,
    demo: Annotated[bool, typer.Option("--demo", help="Serve fake demo data.")] = False,
    dev: Annotated[
        bool,
        typer.Option("--dev", help="Open the Vite dev server (port 5173) instead."),
    ] = False,
) -> None:
    """Start the local server and open the dashboard."""
    if demo:
        console.print("[yellow]Demo mode is not implemented yet (phase 3).[/yellow]")

    token = generate_token()
    base = VITE_DEV_URL if dev else f"http://{HOST}:{port}"
    url = f"{base}/#t={token}"

    config = uvicorn.Config(
        create_app(token=token, port=port),
        host=HOST,
        port=port,
        log_level="info",
        server_header=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)

    console.print(f"[bold]Mecha[/bold] {__version__} on http://{HOST}:{port}")
    console.print(f"Open: {url}")
    if not no_open:
        threading.Thread(target=_open_when_ready, args=(server, url), daemon=True).start()

    try:
        server.run()
    except OSError as exc:  # pragma: no cover - e.g. port already in use
        console.print(f"[red]Could not start server:[/red] {exc}")
        raise typer.Exit(EXIT_FAILURE) from exc
    if not server.started:
        raise typer.Exit(EXIT_FAILURE)


def main() -> None:
    app()
