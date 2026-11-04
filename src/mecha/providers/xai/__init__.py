"""xAI plugin. Passive checks are free: ``GET /v1/api-key`` describes the key itself;
management keys use ``GET /auth/management-keys/validation`` on the management API."""

from __future__ import annotations

import httpx

from mecha.core.models import Credential, CredentialKind, ProviderId
from mecha.core.status import Observation
from mecha.providers.base import ProviderContext, json_body, observation, request, trace
from mecha.providers.xai.errors import map_api_key_info, map_error, map_management_validation

API = "https://api.x.ai"
MANAGEMENT = "https://management-api.x.ai"


class XAIProvider:
    id = ProviderId.xai
    allowed_hosts = frozenset({"api.x.ai", "management-api.x.ai"})

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


PROVIDER = XAIProvider()
