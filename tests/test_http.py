"""Outbound client: allowed hosts only, HTTPS only, no redirects, no headers in logs."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from mecha.core.http import DisallowedRequestError, HttpSettings, NetworkError, make_client, send
from mecha.providers import ENABLED, get_provider

pytestmark = pytest.mark.anyio

FAST = HttpSettings(timeout_seconds=1, retries=3, backoff_seconds=0)


async def no_sleep(_: float) -> None:
    return None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/v1/models",
        "https://api.anthropic.com.evil.example/v1/models",
        "https://api.openai.com/v1/models",  # allowed for OpenAI, not for this client
        "https://api.anthropic.com:8443/v1/models",
        "http://api.anthropic.com/v1/models",
    ],
)
async def test_rejects_any_other_destination(url: str) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, json={}))
        async with make_client({"api.anthropic.com"}, FAST) as client:
            with pytest.raises(DisallowedRequestError):
                await send(client, "GET", url, settings=FAST, sleep=no_sleep)
        assert not route.called, "nothing may leave the machine"


async def test_allowed_host_passes() -> None:
    with respx.mock as router:
        router.get("https://api.anthropic.com/v1/models").mock(return_value=httpx.Response(200))
        async with make_client({"api.anthropic.com"}, FAST) as client:
            ex = await send(client, "GET", "https://api.anthropic.com/v1/models", settings=FAST)
    assert ex.response.status_code == 200
    assert ex.attempts == 1


async def test_redirects_are_never_followed() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.anthropic.com/v1/models").mock(
            return_value=httpx.Response(302, headers={"location": "https://evil.example/steal"})
        )
        evil = router.get("https://evil.example/steal").mock(return_value=httpx.Response(200))
        async with make_client({"api.anthropic.com"}, FAST) as client:
            ex = await send(client, "GET", "https://api.anthropic.com/v1/models", settings=FAST)
    assert ex.response.status_code == 302
    assert not evil.called


async def test_retries_5xx_then_fails() -> None:
    delays: list[float] = []

    async def record(d: float) -> None:
        delays.append(d)

    settings = HttpSettings(timeout_seconds=1, retries=3, backoff_seconds=0.5)
    with respx.mock as router:
        route = router.get("https://api.x.ai/v1/api-key").mock(return_value=httpx.Response(503))
        async with make_client({"api.x.ai"}, settings) as client:
            with pytest.raises(NetworkError, match="HTTP 503 after 4 attempts"):
                await send(
                    client, "GET", "https://api.x.ai/v1/api-key", settings=settings, sleep=record
                )
    assert route.call_count == 4
    assert delays == [0.5, 1.0, 2.0]  # exponential backoff


async def test_retries_timeouts_and_recovers() -> None:
    with respx.mock as router:
        router.get("https://api.x.ai/v1/api-key").mock(
            side_effect=[httpx.ConnectTimeout("t"), httpx.ConnectError("c"), httpx.Response(200)]
        )
        async with make_client({"api.x.ai"}, FAST) as client:
            ex = await send(
                client, "GET", "https://api.x.ai/v1/api-key", settings=FAST, sleep=no_sleep
            )
    assert ex.attempts == 3


async def test_4xx_is_not_retried() -> None:
    with respx.mock as router:
        route = router.get("https://api.x.ai/v1/api-key").mock(return_value=httpx.Response(429))
        async with make_client({"api.x.ai"}, FAST) as client:
            ex = await send(
                client, "GET", "https://api.x.ai/v1/api-key", settings=FAST, sleep=no_sleep
            )
    assert ex.response.status_code == 429
    assert route.call_count == 1


async def test_headers_never_reach_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    secret = "xai-HEADERCANARY" + "x" * 60
    caplog.set_level(logging.DEBUG)
    with respx.mock as router:
        router.get("https://api.x.ai/v1/api-key").mock(return_value=httpx.Response(200, json={}))
        async with make_client({"api.x.ai"}, FAST) as client:
            await send(
                client,
                "GET",
                "https://api.x.ai/v1/api-key",
                headers={"authorization": f"Bearer {secret}"},
                settings=FAST,
            )
    assert "HEADERCANARY" not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_honours_proxy_and_ca_settings_but_never_redirects() -> None:
    client = make_client({"api.x.ai"})
    assert client.trust_env is True  # HTTPS_PROXY / SSL_CERT_FILE for corporate networks
    assert client.follow_redirects is False
    assert client.auth is None  # no .netrc: httpx only uses it with an explicit NetRCAuth


async def test_guard_still_blocks_other_hosts_behind_a_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:3128")
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200))
        async with make_client({"api.x.ai"}, FAST) as client:
            with pytest.raises(DisallowedRequestError):
                await send(client, "GET", "https://evil.example/", settings=FAST, sleep=no_sleep)
            ok = await send(client, "GET", "https://api.x.ai/v1/api-key", settings=FAST)
    assert ok.response.status_code == 200
    assert route.call_count == 1


def test_client_requires_hosts() -> None:
    with pytest.raises(ValueError, match="at least one"):
        make_client(set())


@pytest.mark.parametrize("provider_id", ENABLED)
def test_every_plugin_declares_official_hosts_only(provider_id: object) -> None:
    provider = get_provider(provider_id)  # type: ignore[arg-type]
    assert provider is not None
    expected = {
        "anthropic": {"api.anthropic.com"},
        "openai": {"api.openai.com"},
        "gemini": {"generativelanguage.googleapis.com"},
        "xai": {"api.x.ai", "management-api.x.ai"},
    }[provider.id.value]
    assert set(provider.allowed_hosts) == expected
