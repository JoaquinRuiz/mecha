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
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from rich.console import Console
from rich.markup import escape
from rich.table import Table

# Typer >= 0.27 vendors Click: catching the separate `click` package's exceptions would miss
# them. tests/test_cli_commands.py pins the resulting exit codes, so a move breaks loudly.
from typer._click import Context as ClickContext
from typer._click.exceptions import UsageError
from typer.core import TyperCommand, TyperGroup
from typer.exceptions import TyperException

from mecha import __version__
from mecha.api.routes import ApiContext
from mecha.core.config import load_config
from mecha.core.demo import DemoService
from mecha.core.detect import Detection
from mecha.core.errors import AmbiguousProviderError, MechaError
from mecha.core.events import EventBus
from mecha.core.logging import install_log_redaction
from mecha.core.models import CredentialKind, Overall, ProviderId
from mecha.core.paths import db_path
from mecha.core.redact import redact_text
from mecha.core.scheduler import Scheduler
from mecha.core.serialize import status_json
from mecha.core.service import CredentialStatus, MechaService
from mecha.core.status import exit_code_for
from mecha.linked import TOOLS
from mecha.server import BackgroundJob, create_app, generate_token
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

# ls and check follow the Nagios convention (0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN).
# Click exits usage errors with 2, which a script would read as "something is red", so for
# those commands usage errors exit with 3 instead.
NAGIOS_COMMANDS = frozenset({"ls", "check"})


class NagiosCommand(TyperCommand):
    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: ClickContext | None = None,
        **extra: Any,
    ) -> ClickContext:
        try:
            return super().make_context(info_name, args, parent=parent, **extra)
        except UsageError as exc:
            exc.exit_code = EXIT_FAILURE
            raise


class MechaGroup(TyperGroup):
    """Root group: a usage error in the group's own options before ``ls``/``check`` is 3 too."""

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: ClickContext | None = None,
        **extra: Any,
    ) -> ClickContext:
        command = next((a for a in args if not a.startswith("-")), None)
        try:
            return super().make_context(info_name, args, parent=parent, **extra)
        except UsageError as exc:
            if command in NAGIOS_COMMANDS:
                exc.exit_code = EXIT_FAILURE
            raise


app = typer.Typer(
    name="mecha",
    cls=MechaGroup,
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


def _error_handler[**P, R](fn: Callable[P, R], *, nagios: bool) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort, TyperException):
            raise
        except MechaError as exc:
            console.print(
                f"[red]error:[/red] {escape(redact_text(exc.message, tuple(_seen_secrets)))}"
            )
            raise typer.Exit(EXIT_FAILURE if nagios else exc.exit_code) from None
        except KeyboardInterrupt:
            raise typer.Exit(130) from None
        except Exception as exc:
            message = redact_text(str(exc), tuple(_seen_secrets))
            console.print(f"[red]unexpected error:[/red] {type(exc).__name__}: {escape(message)}")
            log.debug("unexpected error", exc_info=True)
            raise typer.Exit(EXIT_FAILURE) from None

    return wrapper


def handle_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Errors exit with their own code (2 = user error, 3 = Mecha failure)."""
    return _error_handler(fn, nagios=False)


def handle_errors_nagios[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """For ls/check: 0/1/2 mean OK/WARNING/CRITICAL, so *every* error exits 3 (UNKNOWN)."""
    return _error_handler(fn, nagios=True)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _passphrase_prompt(message: str, confirm: bool) -> str:
    value: str = typer.prompt(message, hide_input=True, confirmation_prompt=confirm, err=True)
    if not value:
        raise typer.Exit(EXIT_FAILURE)
    return value


def _prompt_provider(default: str | None) -> ProviderId:
    names = ", ".join(p.value for p in ProviderId)
    while True:
        value: str = typer.prompt(f"Provider ({names})", default=default, err=True)
        try:
            return ProviderId(value.strip().lower())
        except ValueError:
            console.print(f"[yellow]Unknown provider {escape(value)!r}.[/yellow]")


def _service() -> MechaService:
    prompt = _passphrase_prompt if _interactive() else None
    return MechaService(lambda: open_store(db_path(), prompt), config=load_config())


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
    "permission_denied": "no permission",
    "billing_error": "billing problem",
    "team_blocked": "team blocked",
    "key_disabled": "key disabled",
    "key_blocked": "key blocked",
    "gemini_ip_restricted": "key restricted to other IP addresses (may work on its server)",
    "gemini_referrer_restricted": "key restricted to other HTTP referrers (may work there)",
    "gemini_android_app_restricted": "key restricted to an Android app (may work there)",
    "gemini_ios_app_restricted": "key restricted to an iOS app (may work there)",
    "gemini_vpc_sc_blocked": "blocked by VPC Service Controls (may work inside the perimeter)",
    "gemini_org_restriction": "blocked by an organization restriction on this network",
    "gemini_tls_policy": "blocked by a TLS policy for this client",
    "gemini_service_blocked": "key not allowed to call the Gemini API",
    "gemini_api_disabled": "Gemini API disabled in the key's project",
    "gemini_billing_disabled": "billing disabled for the key's project",
    "gemini_project_suspended": "the key's project is suspended",
    "gemini_project_invalid": "the key's project is invalid or deleted",
    "gemini_org_policy_blocked": "Gemini API blocked by an organization policy",
    "gemini_iam_denied": "the key's service account lacks permission",
    "probe_misconfigured": "probe misconfigured (model not found)",
    "probe_uncertain": "probe timed out after sending (may have been billed)",
    "linked_file_unreadable": "linked session file missing or unreadable",
    "gemini_aq_rejected": "AQ. key rejected; if it works in AI Studio, see the known AQ. issue",
}

WARNING_TEXT = {
    "ambiguous_provider": "provider is ambiguous: confirm it",
    "refresh_token_not_stored": "looks like an OAuth refresh token: never stored",
    "retiring_key_type": "key type being retired by the provider",
    "session_file_use_link": "session file: link it by path instead",
    "unknown_format": "unknown format",
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


def _ago(value: datetime, now: datetime) -> str:
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{seconds // size} {unit} ago"
    return "just now"  # pragma: no cover


def _why(item: CredentialStatus, now: datetime) -> str:
    text = ", ".join(REASON_TEXT.get(r.code, r.code) for r in item.result.reasons)
    if item.result.overall is Overall.grey and item.last_known is not None:
        known = item.last_known
        text += f" · last {STATUS_TEXT[known.overall][2]} {_ago(known.checked_at, now)}"
    return text


def _checked(item: CredentialStatus, now: datetime) -> str:
    if item.result.mode == "offline":
        return "never"
    text = _ago(item.result.checked_at, now)
    return f"{text} (cached)" if item.cached else text


def _status_json(item: CredentialStatus) -> dict[str, Any]:
    return status_json(item)


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


class _MechaServer(uvicorn.Server):
    """Closes the event streams as soon as shutdown starts: uvicorn waits for open
    connections to finish, and an SSE stream would otherwise keep Ctrl+C hanging."""

    def __init__(self, config: uvicorn.Config, bus: EventBus) -> None:
        super().__init__(config)
        self._bus = bus

    def handle_exit(self, sig: int, frame: Any) -> None:
        self._bus.close()
        super().handle_exit(sig, frame)


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
    token = generate_token()
    base = VITE_DEV_URL if dev else f"http://{HOST}:{port}"
    url = f"{base}/#t={token}"

    background: list[BackgroundJob] = []
    service: MechaService = DemoService(load_config()) if demo else _service()
    try:
        _ = service.store  # unlock now (keyring / passphrase / TTY prompt), never from the browser
    except MechaError as exc:
        console.print(f"[red]error:[/red] {escape(exc.message)}")
        raise typer.Exit(EXIT_FAILURE) from None
    api = ApiContext(service=service, demo=demo)

    def publish(item: CredentialStatus, done: int, total: int) -> None:
        api.bus.publish(
            {"type": "check_progress", "id": item.credential.id, "done": done, "total": total}
        )

    if demo:
        console.print("[yellow]Demo mode:[/yellow] fake data, no secrets, no network.")
    elif service.config.scheduler.enabled:
        background.append(Scheduler(service, on_result=publish).run_forever)
        hours = service.config.scheduler.interval_hours
        console.print(f"Scheduler: passive checks every {hours:g} h")

    config = uvicorn.Config(
        create_app(token=token, port=port, background=background, api=api),
        host=HOST,
        port=port,
        log_level="info",
        server_header=False,
        proxy_headers=False,
        # Backstop: never wait more than a few seconds for open connections on Ctrl+C.
        timeout_graceful_shutdown=3,
    )
    server = _MechaServer(config, api.bus)

    console.print(f"[bold]Mecha[/bold] {__version__} on http://{HOST}:{port}")
    console.print(f"Open: {url}")
    if not no_open:
        threading.Thread(target=_open_when_ready, args=(server, url), daemon=True).start()

    try:
        server.run()
    except OSError as exc:  # pragma: no cover - e.g. port already in use
        console.print(f"[red]Could not start server:[/red] {exc}")
        raise typer.Exit(EXIT_FAILURE) from exc
    finally:
        service.close()
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
        "Pattern",
        f"{det.pattern_id or '—'}"
        + ("" if det.verified else " [dim](format not in official docs)[/dim]"),
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
            choice = _prompt_provider(exc.candidates[0] if exc.candidates else None)
            item = service.add(
                raw,
                label=label,
                tags=tag or [],
                notes=notes,
                provider=choice,
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


@app.command("ls", cls=NagiosCommand)
@handle_errors_nagios
def ls_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List credentials with their status.

    Exit code (Nagios): 0 green/grey/local, 1 amber, 2 red, 3 any error (usage errors too).
    """
    service = _service()
    try:
        items = service.list()
    finally:
        service.close()
    _report(items, as_json=as_json, code=exit_code_for([i.result.overall for i in items]))


def _report(items: list[CredentialStatus], *, as_json: bool, code: int) -> None:
    now = datetime.now(UTC)
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


@app.command("check", cls=NagiosCommand)
@handle_errors_nagios
def check_cmd(
    ids: Annotated[
        list[str] | None, typer.Argument(metavar="[ID]...", help="IDs or prefixes (default: all).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat grey (could not check) as failure: exit 3.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Check now: ignore the minimum interval and, with --active, the one-probe-"
            "per-hour limit (each probe costs a fraction of a cent).",
        ),
    ] = False,
    active: Annotated[
        bool,
        typer.Option("--active", help="Use the paid active probe (API keys only; never retried)."),
    ] = False,
) -> None:
    """Check credentials (free endpoints by default; --active spends a fraction of a cent).

    Exit code (Nagios): 0 green/grey/local, 1 amber, 2 red, 3 any error; with --strict a
    grey result exits 3 (red still wins). Results younger than the minimum interval are
    reused and marked as cached unless --force. Active probes are limited to one per
    credential and hour; --force skips that limit explicitly.
    """
    service = _service()
    try:
        items = service.check_sync(
            ids or [], force=force, active=active, force_probe=active and force
        )
    finally:
        service.close()
    for item in items:
        if item.probe_next_at is not None:
            console.print(
                f"[yellow]{escape(item.credential.label)}:[/yellow] one active probe per hour; "
                f"next allowed {item.probe_next_at:%H:%M} UTC (use --force to probe now)."
            )
    code = exit_code_for([i.result.overall for i in items], strict=strict)
    _report(items, as_json=as_json, code=code)


def _unique_prefix_len(ids: list[str], minimum: int = 10) -> int:
    """Shortest prefix length that tells every ID apart (ULIDs start with a timestamp)."""
    for n in range(minimum, 27):
        if len({i[:n] for i in ids}) == len(ids):
            return n
    return 26


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
    width_id = _unique_prefix_len([i.credential.id for i in items])
    for i in items:
        c = i.credential
        icon, colour, text = STATUS_TEXT[i.result.overall]
        exp = i.result.expiry
        expires = _fmt_date(exp.value.expires_at, now) if exp.value.expires_at else "unknown"
        if show_why and exp.value.expires_at:
            expires += f" {exp.source}"
        row = [
            c.id[:width_id],
            f"[{colour}]{icon} {text}[/{colour}]",
            escape(c.label),
            f"{c.provider.value}/{KIND_SHORT[c.kind]}",
        ]
        if show_key:
            row.append(escape(c.redacted))
        row.append(expires)
        if show_funds:
            row += [_funds_cell(i), _checked(i, now)]
        if show_why:
            row.append(_why(i, now))
        table.add_row(*row)
    return table


@app.command("link")
@handle_errors
def link_cmd(
    tool: Annotated[str, typer.Argument(help="codex, claude-code or gemini-cli")],
    path: Annotated[
        str | None, typer.Option("--path", help="Session file (default: the tool's).")
    ] = None,
    label: Annotated[str | None, typer.Option(help="Display name.")] = None,
) -> None:
    """Link a CLI's OAuth session file by path: read-only, re-read on every check, tokens
    never copied."""
    if tool not in TOOLS:
        console.print(f"[red]error:[/red] unknown tool {escape(tool)!r} (use: {', '.join(TOOLS)}).")
        raise typer.Exit(EXIT_RED)
    service = _service()
    try:
        item = service.link(tool, path, label=label)
    finally:
        service.close()
    c = item.credential
    console.print(f"[green]Linked[/green] {c.id}  {escape(c.label)}  {escape(c.redacted)}")
    console.print(f"Status: {_status_cell(item)}")


@app.command("import")
@handle_errors
def import_cmd(
    file: Annotated[str, typer.Argument(metavar="FILE", help="A .env file.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Import without asking.")] = False,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Variable name to import (repeatable).")
    ] = None,
) -> None:
    """Import keys from a .env: shows the detected ones (redacted), then imports them."""
    try:
        content = Path(file).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        console.print(f"[red]error:[/red] cannot read {escape(file)}.")
        raise typer.Exit(EXIT_RED) from exc
    service = _service()
    try:
        entries = service.preview_env(content)
        if not entries:
            console.print("No credentials found in that file.")
            return
        table = Table(show_edge=False, pad_edge=False, header_style="bold", box=None)
        for col in ("#", "Variable", "Key", "Detected", "Import"):
            table.add_column(col, no_wrap=True)
        chosen: list[int] = []
        for e in entries:
            wanted = e["importable"] and (not only or e["name"] in only)
            if wanted:
                chosen.append(int(e["index"]))  # type: ignore[call-overload]
            reason = "yes" if wanted else ("duplicate" if e["duplicate_of"] else "no")
            detected = f"{e['provider'] or '?'} · {e['kind'] or '?'}"
            table.add_row(
                str(e["line"]), escape(str(e["name"])), escape(str(e["redacted"])), detected, reason
            )
        out.print(table)
        if not chosen:
            console.print("Nothing to import.")
            return
        if not yes:
            if not _interactive():
                console.print("[red]error:[/red] refusing to import without --yes (no TTY).")
                raise typer.Exit(EXIT_RED)
            typer.confirm(f"Import {len(chosen)} credential(s)?", abort=True, err=True)
        results = service.import_env(content, chosen)
    finally:
        service.close()
    for res in results:
        if res["ok"]:
            console.print(f"[green]Imported[/green] {res['id']}")
        else:
            why = escape(str(res.get("message", res["code"])))
            console.print(f"[yellow]Skipped[/yellow] #{res['index']}: {why}")


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
