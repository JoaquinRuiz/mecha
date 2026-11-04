"""Command line interface (Typer). ``mecha`` with no subcommand runs ``serve``.

Secrets are read only from a hidden prompt or stdin, never from command-line arguments
(they would end up in shell history and in the process list).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any

import click
import typer
import uvicorn
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from mecha import __version__
from mecha.core.detect import Detection
from mecha.core.errors import AmbiguousProviderError, MechaError
from mecha.core.logging import install_log_redaction
from mecha.core.models import CredentialKind, Overall, ProviderId
from mecha.core.paths import db_path
from mecha.core.redact import redact_text
from mecha.core.service import CredentialStatus, MechaService
from mecha.core.status import exit_code_for
from mecha.server import create_app, generate_token
from mecha.store.db import open_store

HOST = "127.0.0.1"
DEFAULT_PORT = 7878
VITE_DEV_URL = "http://localhost:5173"

# Exit codes shared by every command (see SPEC.md, "CLI").
EXIT_OK = 0
EXIT_AMBER = 1
EXIT_RED = 2
EXIT_FAILURE = 3

JSON_SCHEMA_VERSION = 1

log = logging.getLogger("mecha")
console = Console(stderr=True, highlight=False)
out = Console(highlight=False, soft_wrap=True)

app = typer.Typer(
    name="mecha",
    help="How much fuse is left on your API keys?",
    no_args_is_help=False,
    add_completion=False,
    pretty_exceptions_enable=False,  # never render tracebacks with Rich…
    pretty_exceptions_show_locals=False,  # …and never with local variables (secrets)
)
passphrase_app = typer.Typer(help="Manage the recovery passphrase of the store.")
app.add_typer(passphrase_app, name="passphrase")

# Secrets read in this process, so error messages can be scrubbed even for unknown formats.
_seen_secrets: list[str] = []


def handle_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, click.exceptions.Exit, click.ClickException, click.Abort):
            raise
        except MechaError as exc:
            console.print(
                f"[red]error:[/red] {escape(redact_text(exc.message, tuple(_seen_secrets)))}"
            )
            raise typer.Exit(exc.exit_code) from None
        except KeyboardInterrupt:
            raise typer.Exit(130) from None
        except Exception as exc:
            message = redact_text(str(exc), tuple(_seen_secrets))
            console.print(f"[red]unexpected error:[/red] {type(exc).__name__}: {escape(message)}")
            log.debug("unexpected error", exc_info=True)
            raise typer.Exit(EXIT_FAILURE) from None

    return wrapper


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _passphrase_prompt(message: str, confirm: bool) -> str:
    value: str = typer.prompt(message, hide_input=True, confirmation_prompt=confirm, err=True)
    if not value:
        raise typer.Exit(EXIT_FAILURE)
    return value


def _service() -> MechaService:
    prompt = _passphrase_prompt if _interactive() else None
    return MechaService(lambda: open_store(db_path(), prompt))


def _read_secret(what: str = "Secret") -> str:
    if sys.stdin.isatty():
        value: str = typer.prompt(f"{what} (input hidden)", hide_input=True, err=True)
    else:
        value = sys.stdin.read()
    if not value.strip():
        console.print("[red]error:[/red] no secret given (type it at the prompt or pipe it).")
        raise typer.Exit(EXIT_RED)
    _seen_secrets.append(value.strip())
    return value


# --- presentation ----------------------------------------------------------------------

STATUS_TEXT = {
    Overall.red: ("✖", "red", "red"),
    Overall.amber: ("▲", "yellow", "amber"),
    Overall.grey: ("●", "bright_black", "grey"),
    Overall.local: ("◆", "cyan", "local only"),
    Overall.green: ("✔", "green", "green"),
}

REASON_TEXT = {
    "not_checked": "not checked",
    "check_failed": "check failed",
    "validity_unknown": "validity unknown",
    "local_only": "local only",
    "valid": "valid",
    "expired": "expired",
    "expires_soon": "expires soon",
    "funds_empty": "no funds",
    "funds_low": "low funds",
    "funds_capped": "spend cap reached",
    "throttled": "rate limited",
    "retiring_key_type": "key type being retired",
    "validity_invalid": "invalid",
    "validity_revoked": "revoked",
    "validity_blocked": "blocked",
}

WARNING_TEXT = {
    "ambiguous_provider": "provider is ambiguous: confirm it",
    "refresh_token_not_stored": "looks like an OAuth refresh token: never stored",
    "retiring_key_type": "key type being retired by the provider",
    "session_file_use_link": "session file: link it by path instead",
    "unknown_format": "unknown format",
    "unverified_pattern": "format not confirmed in official docs",
    "jwt_unverified": "JWT decoded without signature verification",
    "jwt_unknown_issuer": "unknown JWT issuer",
    "malformed_jwt": "malformed JWT",
}


KIND_SHORT = {
    CredentialKind.api_key: "key",
    CredentialKind.admin_key: "admin",
    CredentialKind.management_key: "mgmt",
    CredentialKind.oauth_token: "oauth",
    CredentialKind.oauth_file: "file",
}


def _status_cell(item: CredentialStatus) -> str:
    icon, colour, text = STATUS_TEXT[item.result.overall]
    reasons = ", ".join(REASON_TEXT.get(r.code, r.code) for r in item.result.reasons)
    return f"[{colour}]{icon} {text}[/{colour}]" + (f" · {reasons}" if reasons else "")


def _fmt_date(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "—"
    days = (value - now).total_seconds() / 86400
    rel = f"{days:.0f}d" if days >= 0 else f"-{-days:.0f}d"
    return f"{value.date().isoformat()} {rel}"


def _funds_cell(item: CredentialStatus) -> str:
    funds = item.result.funds.value
    if funds.state.value == "not_applicable":
        return "n/a"
    if funds.amount is not None:
        return f"{funds.amount} {funds.currency or ''}".strip()
    return funds.state.value


def _parse_expires(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter("use YYYY-MM-DD or an ISO 8601 date-time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _dump(data: Any) -> None:
    out.print_json(json.dumps(data, ensure_ascii=False), highlight=False)


def _status_json(item: CredentialStatus) -> dict[str, Any]:
    return {
        "credential": item.credential.model_dump(mode="json"),
        "result": item.result.model_dump(mode="json"),
    }


# --- commands --------------------------------------------------------------------------


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


def _print_detection(det: Detection) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Format", det.format)
    table.add_row("Provider", det.provider.value if det.provider else "unknown")
    table.add_row("Type", f"{det.label or '—'} ({det.kind.value if det.kind else 'unknown'})")
    table.add_row("Redacted", det.redacted)
    table.add_row(
        "Pattern", f"{det.pattern_id or '—'} · {'verified' if det.verified else 'unverified'}"
    )
    table.add_row("Storable", "yes" if det.storable else "no")
    if det.jwt:
        j = det.jwt
        table.add_row("Issuer", j.issuer or "—")
        table.add_row("Expires", j.expires_at.isoformat() if j.expires_at else "—")
        if j.email:
            table.add_row("Email", j.email)
        if j.plan:
            table.add_row("Plan", j.plan)
        if j.subscription_until:
            table.add_row("Subscription until", j.subscription_until.isoformat())
    for w in det.warnings:
        table.add_row("[yellow]Note[/yellow]", WARNING_TEXT.get(w, w))
    out.print(table)


@app.command("detect")
@handle_errors
def detect_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Classify a secret read from stdin (or a hidden prompt). No network, nothing stored."""
    det = MechaService.detect(_read_secret())
    if as_json:
        _dump(det.model_dump(mode="json"))
    else:
        _print_detection(det)


@app.command()
@handle_errors
def add(
    label: Annotated[str | None, typer.Option(help="Display name.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Tag (repeatable).")] = None,
    notes: Annotated[str | None, typer.Option(help="Free-form notes.")] = None,
    provider: Annotated[
        ProviderId | None, typer.Option(help="Provider, when detection is ambiguous or unknown.")
    ] = None,
    kind: Annotated[
        CredentialKind | None, typer.Option(help="Credential kind, e.g. management_key.")
    ] = None,
    expires: Annotated[
        str | None, typer.Option(help="Manual expiry date (YYYY-MM-DD or ISO 8601).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Add a credential. The secret is read from a hidden prompt or from stdin."""
    expires_at = _parse_expires(expires)
    raw = _read_secret()
    service = _service()
    try:
        try:
            item = service.add(
                raw,
                label=label,
                tags=tag or [],
                notes=notes,
                provider=provider,
                kind=kind,
                manual_expires_at=expires_at,
            )
        except AmbiguousProviderError as exc:
            if not _interactive():
                raise
            choice = typer.prompt(
                "Provider (ambiguous key)",
                default=exc.candidates[0] if exc.candidates else None,
                type=click.Choice([p.value for p in ProviderId]),
                err=True,
            )
            item = service.add(
                raw,
                label=label,
                tags=tag or [],
                notes=notes,
                provider=ProviderId(choice),
                kind=kind,
                manual_expires_at=expires_at,
            )
    finally:
        service.close()

    if as_json:
        _dump(_status_json(item))
        return
    c = item.credential
    console.print(
        f"[green]Added[/green] {c.id}  {escape(c.label)}  {c.provider.value}/{c.kind.value}  "
        f"{escape(c.redacted)}"
    )
    console.print(f"Status: {_status_cell(item)}")


@app.command("ls")
@handle_errors
def ls_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List credentials with their status. Exit code: 0 ok/grey, 1 amber, 2 red, 3 error."""
    service = _service()
    try:
        items = service.list()
    finally:
        service.close()
    now = datetime.now(UTC)
    code = exit_code_for([i.result.overall for i in items])

    if as_json:
        _dump(
            {
                "schema_version": JSON_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "exit_code": code,
                "credentials": [_status_json(i) for i in items],
            }
        )
    elif not items:
        console.print("No credentials yet. Add one with [bold]mecha add[/bold].")
    else:
        out.print(_ls_table(items, now, out.width))
    raise typer.Exit(code)


def _ls_table(items: list[CredentialStatus], now: datetime, width: int) -> Table:
    """Columns by priority so status text is never truncated (never colour only)."""
    show_funds = width >= 100
    show_why = width >= 120
    show_key = width >= 150
    table = Table(show_edge=False, pad_edge=False, header_style="bold", box=None)
    table.add_column("ID", no_wrap=True, min_width=10)
    table.add_column("Status", no_wrap=True, min_width=12)
    table.add_column("Label", overflow="ellipsis", no_wrap=True, max_width=24)
    table.add_column("Type", no_wrap=True)
    if show_key:
        table.add_column("Key", no_wrap=True)
    table.add_column("Expires", no_wrap=True)
    if show_funds:
        table.add_column("Funds", no_wrap=True)
        table.add_column("Checked", no_wrap=True)
    if show_why:
        table.add_column("Why", overflow="fold")
    for i in items:
        c = i.credential
        icon, colour, text = STATUS_TEXT[i.result.overall]
        exp = i.result.expiry
        expires = _fmt_date(exp.value.expires_at, now) if exp.value.expires_at else "unknown"
        if show_why and exp.value.expires_at:
            expires += f" {exp.source}"
        row = [
            c.id[:10],
            f"[{colour}]{icon} {text}[/{colour}]",
            escape(c.label),
            f"{c.provider.value}/{KIND_SHORT[c.kind]}",
        ]
        if show_key:
            row.append(escape(c.redacted))
        row.append(expires)
        if show_funds:
            checked = i.result.checked_at.strftime("%Y-%m-%d %H:%M")
            row += [_funds_cell(i), "never" if i.result.mode == "offline" else checked]
        if show_why:
            row.append(", ".join(REASON_TEXT.get(r.code, r.code) for r in i.result.reasons))
        table.add_row(*row)
    return table


@app.command("rm")
@handle_errors
def rm_cmd(
    credential_id: Annotated[str, typer.Argument(metavar="ID", help="Credential ID or prefix.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete a credential and its history."""
    service = _service()
    try:
        item = service.get(credential_id)
        c = item.credential
        if not yes:
            if not _interactive():
                console.print("[red]error:[/red] refusing to delete without --yes (no TTY).")
                raise typer.Exit(EXIT_RED)
            typer.confirm(f"Delete {c.id} ({c.label}, {c.redacted})?", abort=True, err=True)
        service.remove(c.id)
    finally:
        service.close()
    console.print(f"[green]Deleted[/green] {c.id}")


@passphrase_app.command("set")
@handle_errors
def passphrase_set() -> None:
    """Set or replace the recovery passphrase (reads MECHA_PASSPHRASE[_FILE] or prompts)."""
    from mecha.store.keys import passphrase_from_env

    service = _service()
    try:
        store = service.store
        new = (
            _passphrase_prompt("New passphrase", True) if _interactive() else passphrase_from_env()
        )
        if not new:
            console.print("[red]error:[/red] no passphrase given.")
            raise typer.Exit(EXIT_FAILURE)
        store.set_passphrase(new)
    finally:
        service.close()
    console.print("[green]Recovery passphrase set.[/green]")


@passphrase_app.command("remove")
@handle_errors
def passphrase_remove() -> None:
    """Remove the recovery passphrase (only if the keyring holds the store key)."""
    service = _service()
    try:
        service.store.remove_passphrase()
    finally:
        service.close()
    console.print("Recovery passphrase removed.")


def main() -> None:
    install_log_redaction()
    level = os.environ.get("MECHA_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    app()
