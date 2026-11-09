"""REST API used by the local frontend (SPEC.md, "Modelo de datos y API REST").

Thin layer over :class:`MechaService`. It never returns a secret: at most the redacted form
and the fingerprint. Errors are ``{code, message}``. ``PATCH`` never accepts the secret:
rotating a key means deleting it and adding the new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mecha.core.capabilities import load_capabilities, load_probes, month_label
from mecha.core.errors import (
    AmbiguousProviderError,
    DetectionError,
    DuplicateCredentialError,
    MechaError,
    NotFoundError,
    StoreLockedError,
)
from mecha.core.events import EventBus
from mecha.core.models import Budget, CredentialKind, ProviderId
from mecha.core.serialize import SCHEMA_VERSION, status_json
from mecha.core.service import CredentialStatus, MechaService
from mecha.linked import TOOLS, LinkedFileError, default_path
from mecha.providers import ENABLED

log = logging.getLogger(__name__)
router = APIRouter()

MAX_SECRET = 8192  # longer than any real key or JWT; bigger bodies are rejected unread
KEEPALIVE_SECONDS = 15.0


@dataclass
class ApiContext:
    service: MechaService
    bus: EventBus = field(default_factory=EventBus)
    demo: bool = False
    check_all_task: asyncio.Task[None] | None = None


def _ctx(request: Request) -> ApiContext:
    ctx: ApiContext | None = getattr(request.app.state, "api", None)
    if ctx is None:
        raise StoreLockedError("The credential store is not open in this server.")
    return ctx


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=MAX_SECRET)


class SecretIn(Strict):
    secret: str = Field(min_length=1, max_length=MAX_SECRET)


class CredentialIn(SecretIn):
    label: str | None = Field(None, max_length=200)
    tags: list[Annotated[str, Field(max_length=64)]] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(None, max_length=4000)
    provider: ProviderId | None = None
    kind: CredentialKind | None = None
    manual_expires_at: datetime | None = None


class BudgetIn(Strict):
    amount: Decimal = Field(gt=0, le=Decimal("1000000"))
    currency: str = Field("USD", min_length=3, max_length=3)
    purchased_at: datetime


class CredentialPatch(Strict):
    """Editable fields. ``null`` clears notes, the manual expiry or the budget. No secret:
    rotating a key means deleting it and adding the new one."""

    label: str | None = Field(None, max_length=200)
    tags: list[Annotated[str, Field(max_length=64)]] | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=4000)
    manual_expires_at: datetime | None = None
    active_probe: bool | None = None
    manual_budget: BudgetIn | None = None


class LinkIn(Strict):
    tool: Literal["codex", "claude-code", "gemini-cli"]
    path: str | None = Field(None, max_length=4096)
    label: str | None = Field(None, max_length=200)


class EnvIn(Strict):
    content: str = Field(min_length=1, max_length=60_000)


class EnvImportIn(EnvIn):
    select: list[int] = Field(max_length=500)
    providers: dict[int, ProviderId] = Field(default_factory=dict)


ERROR_STATUS: list[tuple[type[MechaError], int]] = [
    (NotFoundError, 404),
    (DuplicateCredentialError, 409),
    (AmbiguousProviderError, 422),
    (DetectionError, 422),
    (StoreLockedError, 503),
    (LinkedFileError, 422),
]


def error_response(exc: MechaError) -> JSONResponse:
    status = next((code for cls, code in ERROR_STATUS if isinstance(exc, cls)), 500)
    body: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if isinstance(exc, AmbiguousProviderError):
        body["candidates"] = exc.candidates
    if isinstance(exc, DuplicateCredentialError):
        body["existing_id"] = exc.existing_id
    return JSONResponse(body, status_code=status)


def _listing(
    ctx: ApiContext, items: list[CredentialStatus], position: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": ctx.service.now().isoformat(),  # the UI computes "3 h ago" from this
        "demo": ctx.demo,
        # Display settings the UI must not hard-code (they live in config.toml).
        "settings": {
            "soon_days": ctx.service.config.thresholds.soon_days,
            "fuse_horizon_days": ctx.service.config.thresholds.fuse_horizon_days,
        },
        "credentials": [status_json(i) for i in items],
        # The event position this listing was computed at (compared with the stream's hello).
        "events": position,
    }


def _changed(ctx: ApiContext, cred_id: str | None = None) -> None:
    ctx.bus.publish({"type": "credentials_changed", "id": cred_id})


@router.get("/credentials")
async def list_credentials(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    position = ctx.bus.position()  # before computing: never newer than the data
    return _listing(ctx, ctx.service.list(), position)


@router.post("/credentials", status_code=201)
async def add_credential(request: Request, body: CredentialIn) -> dict[str, Any]:
    ctx = _ctx(request)
    item = ctx.service.add(
        body.secret,
        label=body.label,
        tags=body.tags,
        notes=body.notes,
        provider=body.provider,
        kind=body.kind,
        manual_expires_at=body.manual_expires_at,
    )
    _changed(ctx, item.credential.id)
    return status_json(item)


@router.post("/detect")
async def detect(body: SecretIn) -> dict[str, Any]:
    """Classify a secret without storing it or touching the network."""
    return MechaService.detect(body.secret).model_dump(mode="json")


@router.patch("/credentials/{cred_id}")
async def patch_credential(request: Request, cred_id: str, body: CredentialPatch) -> dict[str, Any]:
    ctx = _ctx(request)
    sent = body.model_fields_set
    clear = [
        f
        for f in ("notes", "manual_expires_at", "manual_budget")
        if f in sent and getattr(body, f) is None
    ]
    budget = body.manual_budget
    manual_budget = (
        Budget(
            amount=budget.amount, currency=budget.currency.upper(), purchased_at=budget.purchased_at
        )
        if budget is not None
        else None
    )
    item = ctx.service.update(
        cred_id,
        label=body.label if "label" in sent else None,
        tags=body.tags if "tags" in sent else None,
        notes=body.notes if "notes" in sent and body.notes is not None else None,
        manual_expires_at=body.manual_expires_at
        if "manual_expires_at" in sent and body.manual_expires_at is not None
        else None,
        active_probe=body.active_probe if "active_probe" in sent else None,
        manual_budget=manual_budget,
        clear=clear,
    )
    _changed(ctx, item.credential.id)
    return status_json(item)


@router.delete("/credentials/{cred_id}", status_code=204)
async def delete_credential(request: Request, cred_id: str) -> Response:
    ctx = _ctx(request)
    cred = ctx.service.remove(cred_id)
    _changed(ctx, cred.id)
    return Response(status_code=204)


@router.post("/credentials/{cred_id}/check")
async def check_credential(
    request: Request,
    cred_id: str,
    mode: Literal["passive", "active"] = "passive",
    force: bool = False,
) -> Any:
    """``mode=active`` spends a fraction of a cent; one probe per hour unless ``force``
    (the UI asks for explicit confirmation first)."""
    ctx = _ctx(request)
    active = mode == "active"
    [item] = await ctx.service.check(
        [cred_id], force=force, active=active, force_probe=active and force
    )
    if active and item.probe_next_at is not None:
        return JSONResponse(
            {
                "code": "probe_too_soon",
                "message": "One active probe per hour. Confirm to probe again now.",
                "next_allowed_at": item.probe_next_at.isoformat(),
            },
            status_code=409,
        )
    _changed(ctx, item.credential.id)
    return status_json(item)


@router.post("/check-all", status_code=202)
async def check_all(request: Request, force: bool = False) -> Any:
    """Start checking every credential; progress arrives on ``/api/events``."""
    ctx = _ctx(request)
    if ctx.check_all_task is not None and not ctx.check_all_task.done():
        return JSONResponse(
            {"code": "already_running", "message": "A check of all credentials is running."},
            status_code=409,
        )
    total = len(ctx.service.store.all())

    def progress(item: CredentialStatus, done: int, of: int) -> None:
        ctx.bus.publish(
            {"type": "check_progress", "id": item.credential.id, "done": done, "total": of}
        )

    async def run() -> None:
        try:
            await ctx.service.check(force=force, on_result=progress)
        except Exception:  # never let a background task die silently
            log.exception("check-all failed")
        finally:
            ctx.bus.publish({"type": "check_all_done"})

    ctx.bus.publish({"type": "check_all_started", "total": total})
    ctx.check_all_task = asyncio.create_task(run())
    return {"started": True, "total": total}


@router.get("/credentials/{cred_id}/history")
async def history(
    request: Request, cred_id: str, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> dict[str, Any]:
    ctx = _ctx(request)
    results = ctx.service.history(cred_id, limit)
    return {"history": [r.model_dump(mode="json") for r in results]}


@router.post("/linked", status_code=201)
async def link(request: Request, body: LinkIn) -> dict[str, Any]:
    """Link a CLI's OAuth session file by path: read-only, tokens never copied."""
    ctx = _ctx(request)
    item = ctx.service.link(body.tool, body.path, label=body.label)
    _changed(ctx, item.credential.id)
    return status_json(item)


@router.get("/linked/candidates")
async def linked_candidates() -> dict[str, Any]:
    """Default session file paths and whether they exist (offered directly in the UI)."""
    out = []
    for tool in TOOLS:
        path = default_path(tool)
        out.append({"tool": tool, "path": str(path), "exists": path.is_file()})
    return {"candidates": out}


@router.post("/import/env/preview")
async def import_env_preview(request: Request, body: EnvIn) -> dict[str, Any]:
    """Detected entries of a pasted .env, redacted. Nothing is stored."""
    return {"entries": _ctx(request).service.preview_env(body.content)}


@router.post("/import/env")
async def import_env(request: Request, body: EnvImportIn) -> dict[str, Any]:
    ctx = _ctx(request)
    results = ctx.service.import_env(body.content, body.select, body.providers)
    if any(r["ok"] for r in results):
        _changed(ctx)
    return {"results": results}


@router.get("/providers")
async def providers(request: Request) -> dict[str, Any]:
    """Capability matrix, limited to what is implemented (no promises), with the estimated
    cost of an active probe (dated price table) per probe and per month."""
    implemented = {"yes", "error_only", "no", "admin_key", "management_key", "admin_estimate"}
    out = []
    for pid, caps in load_capabilities().items():
        if pid not in ENABLED:
            continue
        out.append(
            {
                "id": pid.value,
                "name": caps.name,
                "validate": caps.validate_,
                "funds": caps.funds if caps.funds in implemented else "no",
                "expiry": caps.expiry if caps.expiry in implemented else "no",
                "kinds": sorted(k.value for k in caps.passive),
                "probe": _probe_info(pid, request),
            }
        )
    return {"providers": out}


def _probe_info(pid: ProviderId, request: Request) -> dict[str, Any] | None:
    probe = load_probes().get(pid)
    if probe is None:
        return None
    ctx: ApiContext | None = getattr(request.app.state, "api", None)
    hours = ctx.service.config.scheduler.interval_hours if ctx else 6.0
    monthly = probe.usd_per_month(hours)
    return {
        "endpoint": probe.endpoint,
        "model": probe.default_model,
        "input_tokens": probe.input_tokens,
        "output_tokens": probe.output_tokens,
        "usd_per_probe": str(probe.usd_per_probe()),
        "usd_per_month": str(monthly),
        "usd_per_month_label": month_label(monthly),
        "interval_hours": hours,
        "prices_checked": probe.prices_checked.isoformat(),
        "prices_source": probe.prices_source,
        "free_probe": probe.free_probe,
    }


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events, read by the frontend with fetch (the token goes in a header).

    Events only say what changed; the UI refetches over REST. A comment line every 15 s
    keeps proxies and the browser from timing the stream out.
    """
    ctx = _ctx(request)

    async def stream() -> AsyncIterator[bytes]:
        yield b": connected\n\n"
        async for event in ctx.bus.subscribe(keepalive=KEEPALIVE_SECONDS):
            if await request.is_disconnected():
                return
            if event is None:
                yield b": keepalive\n\n"
            else:
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

    headers = {"cache-control": "no-store", "x-accel-buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


async def cancel_background(ctx: ApiContext) -> None:
    task = ctx.check_all_task
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
