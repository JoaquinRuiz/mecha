"""xAI plugin. Passive checks are free: ``GET /v1/api-key`` describes the key itself;
management keys use ``GET /auth/management-keys/validation`` on the management API."""

from __future__ import annotations

import httpx

from mecha.core.http import Route
from mecha.core.models import Credential, CredentialKind, ProviderId, Validity
from mecha.core.status import Observation
from mecha.providers.base import (
    Finding,
    ProviderContext,
    json_body,
    observation,
    probe_misconfigured,
    probe_observation,
    request,
    trace,
)
from mecha.providers.xai.errors import map_api_key_info, map_error, map_management_validation

API = "https://api.x.ai"
MANAGEMENT = "https://management-api.x.ai"


API_HOST = "api.x.ai"
MGMT_HOST = "management-api.x.ai"
PROBE_DEFAULT = "grok-4.20-0309-non-reasoning"  # cheapest non-reasoning chat model
ID = r"[A-Za-z0-9_.\-]+"
ROUTES: dict[CredentialKind, tuple[Route, ...]] = {
    # A normal key never reaches the management API...
    CredentialKind.api_key: (
        Route("GET", API_HOST, "/v1/api-key"),
        Route("GET", API_HOST, "/v1/models"),  # free: picks the probe model
        Route("POST", API_HOST, "/v1/chat/completions"),  # active probe, minimal
    ),
    # ...and a management key (which could create or delete keys) only reaches it, for the
    # verified reads.
    CredentialKind.management_key: (
        Route("GET", MGMT_HOST, "/auth/management-keys/validation"),
        Route("GET", MGMT_HOST, f"/auth/teams/{ID}/api-keys"),
        Route("GET", MGMT_HOST, f"/v1/billing/teams/{ID}/prepaid/balance"),
        Route("GET", MGMT_HOST, f"/v1/billing/teams/{ID}/postpaid/spending-limits"),
        Route("GET", MGMT_HOST, f"/v1/billing/teams/{ID}/postpaid/invoice/preview"),
    ),
}


class XAIProvider:
    id = ProviderId.xai
    allowed_hosts = frozenset({API_HOST, MGMT_HOST})

    def routes(self, kind: CredentialKind) -> tuple[Route, ...]:
        return ROUTES.get(kind, ())

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict[str, str | int]]:
        if kind is CredentialKind.management_key:
            return MANAGEMENT, "/auth/management-keys/validation", {}
        return API, "/v1/api-key", {}

    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        management = cred.kind is CredentialKind.management_key
        base, path = (
            (MANAGEMENT, "/auth/management-keys/validation")
            if management
            else (
                API,
                "/v1/api-key",
            )
        )
        result = await request(
            http, ctx, "GET", base + path, headers={"authorization": f"Bearer {secret}"}
        )
        if isinstance(result, Observation):
            return result
        response = result.response
        body = json_body(response)
        if response.is_success and body is not None:
            finding = map_management_validation(body) if management else map_api_key_info(body)
        else:
            finding = map_error(response.status_code, body)
        return observation(finding, now=ctx.now, http=trace(f"GET {path}", result))

    async def _probe_model(
        self, http: httpx.AsyncClient, ctx: ProviderContext, headers: dict[str, str]
    ) -> str | Observation:
        if ctx.probe_model != "auto":
            return ctx.probe_model
        if ctx.cached_probe_model:
            return ctx.cached_probe_model
        listed = await request(http, ctx, "GET", API + "/v1/models", headers=headers)
        if isinstance(listed, Observation):
            return listed
        ids = [
            str(m.get("id"))
            for m in (json_body(listed.response) or {}).get("data") or []
            if isinstance(m, dict)
        ]
        if PROBE_DEFAULT in ids:
            return PROBE_DEFAULT
        cheap = sorted(i for i in ids if "non-reasoning" in i)  # no thinking tokens
        return cheap[0] if cheap else PROBE_DEFAULT

    async def check_active(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        """A minimal POST /v1/chat/completions (max_tokens 1)."""
        headers = {"authorization": f"Bearer {secret}"}
        model = await self._probe_model(http, ctx, headers)
        if isinstance(model, Observation):
            return model
        path = "/v1/chat/completions"
        payload = {"model": model, "messages": [{"role": "user", "content": "."}], "max_tokens": 1}
        result = await request(http, ctx, "POST", API + path, headers=headers, json=payload)
        if isinstance(result, Observation):
            return result.model_copy(update={"mode": "active", "probe_model": model})
        response, body = result.response, json_body(result.response)
        http_trace = trace(f"POST {path}", result)
        if response.status_code == 404:
            return probe_misconfigured(ctx.now, http_trace, model)
        finding = (
            Finding(validity=Validity.valid)
            if response.is_success
            else map_error(response.status_code, body)
        )
        return probe_observation(
            finding, now=ctx.now, http=http_trace, limits=None, model=model, paid=True
        )


PROVIDER = XAIProvider()
