"""Anthropic plugin. Passive check: ``GET /v1/models`` (admin keys: list API keys, limit 1)."""

from __future__ import annotations

import httpx

from mecha.core.http import Route
from mecha.core.models import Credential, CredentialKind, ProviderId, Validity
from mecha.core.status import Observation
from mecha.providers.anthropic.errors import map_error
from mecha.providers.base import (
    Finding,
    ProviderContext,
    json_body,
    observation,
    parse_rate_limits,
    probe_misconfigured,
    probe_observation,
    request,
    trace,
)

BASE = "https://api.anthropic.com"
PROBE_DEFAULT = "claude-haiku-5-5"  # fallback when the model list has no haiku line


HOST = "api.anthropic.com"
ROUTES: dict[CredentialKind, tuple[Route, ...]] = {
    CredentialKind.api_key: (
        Route("GET", HOST, "/v1/models"),
        Route("POST", HOST, "/v1/messages"),  # active probe, max_tokens 1
        Route("POST", HOST, "/v1/messages/count_tokens"),  # free probe (opt-in)
    ),
    # An admin key could create or archive keys: verified reads only.
    CredentialKind.admin_key: (Route("GET", HOST, "/v1/organizations/api_keys"),),
}


class AnthropicProvider:
    id = ProviderId.anthropic
    allowed_hosts = frozenset({HOST})

    def routes(self, kind: CredentialKind) -> tuple[Route, ...]:
        return ROUTES.get(kind, ())

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict[str, str | int]]:
        if kind is CredentialKind.admin_key:
            return BASE, "/v1/organizations/api_keys", {"limit": 1}
        return BASE, "/v1/models", {"limit": 1000}

    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        base, path, params = self.endpoint(cred.kind)
        headers = {"x-api-key": secret, "anthropic-version": ctx.anthropic_version}
        result = await request(http, ctx, "GET", base + path, headers=headers, params=params)
        if isinstance(result, Observation):
            return result
        response = result.response
        body = json_body(response)
        limits = parse_rate_limits(
            response.headers, ctx.now, prefix="anthropic-ratelimit-", tokens="tokens"
        )
        if response.is_success and body is not None:
            finding = Finding(validity=Validity.valid)
            if cred.kind is not CredentialKind.admin_key:
                finding.account["models"] = str(len(body.get("data") or []))
            org = response.headers.get("anthropic-organization-id")
            if org:
                finding.account["org_id"] = org
                finding.account_source["org_id"] = "header"
        else:
            finding = map_error(response.status_code, body)
        return observation(finding, now=ctx.now, http=trace(f"GET {path}", result), limits=limits)

    async def _probe_model(
        self, http: httpx.AsyncClient, ctx: ProviderContext, headers: dict[str, str]
    ) -> str | Observation:
        if ctx.probe_model != "auto":
            return ctx.probe_model
        if ctx.cached_probe_model:
            return ctx.cached_probe_model
        listed = await request(
            http, ctx, "GET", BASE + "/v1/models", headers=headers, params={"limit": 1000}
        )
        if isinstance(listed, Observation):
            return listed
        models = (json_body(listed.response) or {}).get("data") or []
        haiku = [
            m
            for m in models
            if isinstance(m, dict)
            and m.get("line") == "haiku"
            and m.get("lifecycle", "active") == "active"
        ]
        haiku.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
        return str(haiku[0]["id"]) if haiku else PROBE_DEFAULT

    async def check_active(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        """POST /v1/messages with max_tokens 1 (or the free count_tokens, opt-in)."""
        headers = {"x-api-key": secret, "anthropic-version": ctx.anthropic_version}
        model = await self._probe_model(http, ctx, headers)
        if isinstance(model, Observation):
            return model
        messages = [{"role": "user", "content": "."}]
        payload: dict[str, object]
        if ctx.probe_free:
            path, payload = "/v1/messages/count_tokens", {"model": model, "messages": messages}
        else:
            path, payload = "/v1/messages", {"model": model, "max_tokens": 1, "messages": messages}
        result = await request(http, ctx, "POST", BASE + path, headers=headers, json=payload)
        if isinstance(result, Observation):
            return result.model_copy(update={"mode": "active", "probe_model": model})
        response, body = result.response, json_body(result.response)
        http_trace = trace(f"POST {path}", result)
        if response.status_code == 404:
            return probe_misconfigured(ctx.now, http_trace, model)
        limits = parse_rate_limits(
            response.headers, ctx.now, prefix="anthropic-ratelimit-", tokens="tokens"
        )
        finding = (
            Finding(validity=Validity.valid)
            if response.is_success
            else map_error(response.status_code, body)
        )
        return probe_observation(
            finding,
            now=ctx.now,
            http=http_trace,
            limits=limits,
            model=model,
            paid=not ctx.probe_free,
        )


PROVIDER = AnthropicProvider()
