"""Outbound HTTP: per-provider host allow-list, HTTPS only, no redirects, no header logging.

Every provider plugin gets a client that can only reach the hosts it declares. Anything else
(another host, plain HTTP, a redirect) raises before a byte leaves the machine.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

import httpx

from mecha import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"mecha-keys/{__version__} (+https://github.com/JoaquinRuiz/mecha)"
RETRY_STATUS = frozenset({500, 502, 503, 504, 529})

# httpx logs request lines at INFO and httpcore logs connection internals at DEBUG. Neither
# should ever be able to print headers (they carry the credentials), so keep them quiet.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class DisallowedRequestError(RuntimeError):
    """The request targeted a host or scheme outside the provider's allow-list."""


class NetworkError(RuntimeError):
    """The request could not complete (network, timeout or repeated 5xx)."""

    def __init__(self, message: str, *, status: int | None = None, latency_ms: float | None = None):
        super().__init__(message)
        self.status = status
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class HttpSettings:
    timeout_seconds: float = 10.0
    retries: int = 3
    backoff_seconds: float = 0.5


def _guard(allowed: frozenset[str]) -> Callable[[httpx.Request], Awaitable[None]]:
    async def check(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedRequestError(f"Only HTTPS is allowed (got {url.scheme}://{url.host}).")
        if url.host not in allowed or url.port not in (None, 443):
            raise DisallowedRequestError(f"Host {url.host} is not allowed for this provider.")

    return check


def make_client(
    allowed_hosts: Iterable[str],
    settings: HttpSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    settings = settings or HttpSettings()
    allowed = frozenset(h.lower() for h in allowed_hosts)
    if not allowed:
        raise ValueError("a provider must declare at least one allowed host")
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.timeout_seconds),
        follow_redirects=False,
        # trust_env honours HTTPS_PROXY/NO_PROXY and SSL_CERT_FILE/SSL_CERT_DIR (corporate
        # proxies, custom CAs). httpx 0.28 never reads .netrc unless given NetRCAuth. The
        # request hook still validates the *destination* host, proxy or not.
        trust_env=True,
        headers={"user-agent": USER_AGENT, "accept": "application/json"},
        event_hooks={"request": [_guard(allowed)]},
        transport=transport,
    )


@dataclass(frozen=True)
class Exchange:
    response: httpx.Response
    latency_ms: float
    attempts: int


async def send(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str | int] | None = None,
    settings: HttpSettings | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Exchange:
    """Send with retries on network errors and 5xx/529 (exponential backoff).

    Redirects are returned as-is (never followed). Raises :class:`NetworkError` when every
    attempt failed, :class:`DisallowedRequestError` for a forbidden destination.
    """
    settings = settings or HttpSettings()
    last_error = "unknown error"
    last_status: int | None = None
    for attempt in range(settings.retries + 1):
        if attempt:
            await sleep(settings.backoff_seconds * 2 ** (attempt - 1))
        started = time.perf_counter()
        try:
            response = await client.request(method, url, headers=headers, params=params)
        except DisallowedRequestError:
            raise
        except httpx.TimeoutException:
            last_error, last_status = "timeout", None
            continue
        except httpx.TransportError as exc:
            last_error, last_status = f"network error ({type(exc).__name__})", None
            continue
        latency = (time.perf_counter() - started) * 1000
        if response.status_code in RETRY_STATUS:
            last_error, last_status = f"HTTP {response.status_code}", response.status_code
            continue
        return Exchange(response, latency, attempt + 1)
    raise NetworkError(f"{last_error} after {settings.retries + 1} attempts", status=last_status)
