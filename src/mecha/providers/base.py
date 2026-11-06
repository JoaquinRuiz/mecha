"""Provider plugin interface and helpers shared by every plugin.

A plugin never raises raw exceptions to the core: it returns an :class:`Observation`.
Network failures become ``check_status = failed`` (grey).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from mecha.core.http import (
    DisallowedRequestError,
    Exchange,
    HttpSettings,
    NetworkError,
    Route,
    send,
)
from mecha.core.models import (
    CheckStatus,
    Credential,
    CredentialKind,
    Expiry,
    ExpiryState,
    Funds,
    FundsState,
    HttpTrace,
    Limits,
    LimitsState,
    Overall,
    ProviderError,
    ProviderId,
    RateWindow,
    Reason,
    Source,
    Validity,
)
from mecha.core.redact import redact_text
from mecha.core.status import Observation

log = logging.getLogger(__name__)

# Response headers worth keeping in the trace (never request headers).
TRACE_HEADERS = re.compile(
    r"^(request-id|x-request-id|anthropic-organization-id|openai-organization|openai-project"
    r"|retry-after|anthropic-ratelimit-.*|x-ratelimit-.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderContext:
    now: datetime
    http: HttpSettings = field(default_factory=HttpSettings)
    anthropic_version: str = "2026-10-21"
    # Active probes: a pinned model ("auto" = cheapest from the free model list), the model
    # used last time (reused to avoid listing models on every probe), and free mode.
    probe_model: str = "auto"
    cached_probe_model: str | None = None
    probe_free: bool = False


class Provider(Protocol):
    id: ProviderId
    allowed_hosts: frozenset[str]

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict[str, str | int]]:
        """Free passive endpoint for ``kind``: (base URL, path, query params)."""
        ...

    def routes(self, kind: CredentialKind) -> tuple[Route, ...]:
        """The closed list of method + host + path this credential kind may request."""
        ...

    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation: ...

    async def check_active(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation: ...


@dataclass
class Finding:
    """Partial result of mapping one response (success or error)."""

    validity: Validity | None = None
    funds: FundsState | None = None
    funds_amount: Any = None
    funds_currency: str | None = None
    expiry: ExpiryState | None = None
    expires_at: datetime | None = None
    limits: LimitsState | None = None
    notes: list[Reason] = field(default_factory=list)
    account: dict[str, str] = field(default_factory=dict)
    account_source: dict[str, Source] = field(default_factory=dict)
    error: ProviderError | None = None
    understood: bool = True  # False: response we cannot interpret -> grey "check failed"

    def amber(self, code: str) -> Finding:
        self.notes.append(Reason(code=code, overall=Overall.amber))
        return self

    def red(self, code: str) -> Finding:
        self.notes.append(Reason(code=code, overall=Overall.red))
        return self


def json_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        value = response.json()
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def contains(text: str | None, *needles: str) -> bool:
    lowered = (text or "").lower()
    return any(n.lower() in lowered for n in needles)


def provider_error(code: str | None, message: str | None) -> ProviderError:
    # Provider messages can echo parts of the request; scrub anything key-shaped.
    return ProviderError(code=code or "error", message=redact_text((message or "")[:500]))


def _duration(value: str) -> timedelta | None:
    """OpenAI-style durations: '1s', '6m0s', '20ms', '1h2m3.5s'."""
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if not parts or "".join(n + u for n, u in parts) != value.strip():
        return None
    seconds = 0.0
    for number, unit in parts:
        seconds += float(number) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return timedelta(seconds=seconds)


def _reset(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass
    delta = _duration(value)
    return now + delta if delta is not None else None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_rate_limits(
    headers: Mapping[str, str], now: datetime, *, prefix: str, tokens: str
) -> Limits | None:
    """Read ``{prefix}{kind}-limit/remaining/reset`` style headers into :class:`Limits`.

    Anthropic: prefix ``anthropic-ratelimit-``, keys ``requests-limit`` etc.
    OpenAI: prefix ``x-ratelimit-``, keys ``limit-requests`` etc.
    """
    h = {k.lower(): v for k, v in headers.items()}

    def window(kind: str) -> RateWindow | None:
        if prefix == "x-ratelimit-":
            keys = (f"{prefix}limit-{kind}", f"{prefix}remaining-{kind}", f"{prefix}reset-{kind}")
        else:
            keys = (f"{prefix}{kind}-limit", f"{prefix}{kind}-remaining", f"{prefix}{kind}-reset")
        limit, remaining, reset = (h.get(k) for k in keys)
        if limit is None and remaining is None:
            return None
        return RateWindow(limit=_int(limit), remaining=_int(remaining), reset=_reset(reset, now))

    requests, token_window = window("requests"), window(tokens)
    if requests is None and token_window is None:
        return None
    exhausted = any(w is not None and w.remaining == 0 for w in (requests, token_window))
    return Limits(
        state=LimitsState.throttled if exhausted else LimitsState.ok,
        requests=requests,
        tokens=token_window,
    )


def trace(endpoint: str, exchange: Exchange) -> HttpTrace:
    kept = {k.lower(): v for k, v in exchange.response.headers.items() if TRACE_HEADERS.match(k)}
    return HttpTrace(
        endpoint=endpoint,
        status=exchange.response.status_code,
        latency_ms=round(exchange.latency_ms, 1),
        headers=kept,
    )


def observation(
    finding: Finding,
    *,
    now: datetime,
    http: HttpTrace | None,
    limits: Limits | None = None,
) -> Observation:
    if not finding.understood:
        return Observation(
            check_status=CheckStatus.failed, observed_at=now, http=http, error=finding.error
        )
    if finding.limits is LimitsState.throttled:
        limits = (
            limits.model_copy(update={"state": LimitsState.throttled})
            if limits
            else Limits(state=LimitsState.throttled)
        )
    funds = None
    if finding.funds is not None:
        funds = Funds(
            state=finding.funds, amount=finding.funds_amount, currency=finding.funds_currency
        )
    expiry = None
    if finding.expiry is not None or finding.expires_at is not None:
        expiry = Expiry(
            state=finding.expiry or ExpiryState.scheduled, expires_at=finding.expires_at
        )
    return Observation(
        check_status=CheckStatus.ok,
        observed_at=now,
        validity=finding.validity,
        funds=funds,
        expiry=expiry,
        limits=limits,
        account=finding.account,
        account_source=finding.account_source,
        notes=finding.notes,
        http=http,
        error=finding.error,
    )


def failed(now: datetime, endpoint: str, exc: Exception) -> Observation:
    status = exc.status if isinstance(exc, NetworkError) else None
    code = "disallowed_request" if isinstance(exc, DisallowedRequestError) else "network"
    uncertain = isinstance(exc, NetworkError) and exc.uncertain
    return Observation(
        check_status=CheckStatus.failed,
        observed_at=now,
        http=HttpTrace(endpoint=endpoint, status=status, latency_ms=None),
        error=ProviderError(code=code, message=redact_text(str(exc))),
        # A paid POST that timed out after sending may have been billed: never retried.
        failure="probe_uncertain" if uncertain else None,
    )


def probe_misconfigured(now: datetime, http: HttpTrace | None, model: str) -> Observation:
    """The probe model no longer exists (404): a configuration problem, not a bad key."""
    return Observation(
        check_status=CheckStatus.failed,
        observed_at=now,
        http=http,
        failure="probe_misconfigured",
        probe_model=model,
        error=ProviderError(code="model_not_found", message=f"Probe model {model!r} not found."),
    )


def probe_observation(
    finding: Finding,
    *,
    now: datetime,
    http: HttpTrace | None,
    limits: Limits | None,
    model: str,
    paid: bool,
) -> Observation:
    """A successful paid probe proves the key can spend: funds are at least "ok"."""
    if paid and finding.understood and finding.funds is None and finding.validity is Validity.valid:
        finding.funds = FundsState.ok
    obs = observation(finding, now=now, http=http, limits=limits)
    return obs.model_copy(update={"mode": "active", "probe_model": model})


async def request(
    http: httpx.AsyncClient,
    ctx: ProviderContext,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str | int] | None = None,
    json: object | None = None,
) -> Exchange | Observation:
    """Send a request; on failure return a grey Observation instead of raising."""
    endpoint = f"{method} {httpx.URL(url).copy_with(query=None)}"
    try:
        return await send(
            http, method, url, headers=headers, params=params, json=json, settings=ctx.http
        )
    except (NetworkError, DisallowedRequestError) as exc:
        if isinstance(exc, DisallowedRequestError):
            log.error("blocked outbound request: %s", exc)
        return failed(ctx.now, endpoint, exc)


def unexpected(status: int, body: dict[str, Any] | None) -> Finding:
    """A response we don't know how to read: grey, with the provider's message kept."""
    message = None
    if body and isinstance(body.get("error"), dict):
        message = body["error"].get("message")
    return Finding(understood=False, error=provider_error(f"http_{status}", message))


def remote_kinds() -> frozenset[CredentialKind]:
    return frozenset(
        {CredentialKind.api_key, CredentialKind.admin_key, CredentialKind.management_key}
    )
