"""Gemini plugin. Passive check: ``GET /v1beta/models``. The key always travels in the
``x-goog-api-key`` header, never in the URL (it would end up in logs)."""

from __future__ import annotations

import httpx

from mecha.core.http import Route
from mecha.core.models import Credential, CredentialKind, ProviderId, Validity
from mecha.core.status import Observation
from mecha.providers.base import Finding, ProviderContext, json_body, observation, request, trace
from mecha.providers.gemini.errors import map_error

BASE = "https://generativelanguage.googleapis.com"


HOST = "generativelanguage.googleapis.com"
MODEL = r"[A-Za-z0-9_.\-]+"
ROUTES: dict[CredentialKind, tuple[Route, ...]] = {
    CredentialKind.api_key: (
        Route("GET", HOST, "/v1beta/models"),
        Route("POST", HOST, f"/v1beta/models/{MODEL}:generateContent"),  # active probe
        Route("POST", HOST, f"/v1beta/models/{MODEL}:countTokens"),  # free probe (opt-in)
    ),
}


class GeminiProvider:
    id = ProviderId.gemini
    allowed_hosts = frozenset({HOST})

    def routes(self, kind: CredentialKind) -> tuple[Route, ...]:
        return ROUTES.get(kind, ())

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict[str, str | int]]:
        return BASE, "/v1beta/models", {"pageSize": 1000}  # docs: at most 1000 per page

    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        base, path, params = self.endpoint(cred.kind)
        result = await request(
            http, ctx, "GET", base + path, headers={"x-goog-api-key": secret}, params=params
        )
        if isinstance(result, Observation):
            return result
        response = result.response
        body = json_body(response)
        if response.is_success and body is not None:
            finding = Finding(validity=Validity.valid)
            finding.account["models"] = str(len(body.get("models") or []))
        else:
            finding = map_error(response.status_code, body)
        return observation(finding, now=ctx.now, http=trace(f"GET {path}", result))


PROVIDER = GeminiProvider()
