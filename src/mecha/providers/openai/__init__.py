"""OpenAI plugin. Passive check: ``GET /v1/models`` (admin keys: list admin keys, limit 1).

``/v1/models`` does not check quota, so a passive check never detects missing funds.
"""

from __future__ import annotations

import httpx

from mecha.core.models import Credential, CredentialKind, ProviderId, Validity
from mecha.core.status import Observation
from mecha.providers.base import (
    Finding,
    ProviderContext,
    json_body,
    observation,
    parse_rate_limits,
    request,
    trace,
)
from mecha.providers.openai.errors import map_error

BASE = "https://api.openai.com"


class OpenAIProvider:
    id = ProviderId.openai
    allowed_hosts = frozenset({"api.openai.com"})

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict[str, str | int]]:
        if kind is CredentialKind.admin_key:
            return BASE, "/v1/organization/admin_api_keys", {"limit": 1}
        return BASE, "/v1/models", {}

    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation:
        base, path, params = self.endpoint(cred.kind)
        headers = {"authorization": f"Bearer {secret}"}
        result = await request(http, ctx, "GET", base + path, headers=headers, params=params)
        if isinstance(result, Observation):
            return result
        response = result.response
        body = json_body(response)
        limits = parse_rate_limits(
            response.headers, ctx.now, prefix="x-ratelimit-", tokens="tokens"
        )
        if response.is_success and body is not None:
            finding = Finding(validity=Validity.valid)
            if cred.kind is not CredentialKind.admin_key:
                finding.account["models"] = str(len(body.get("data") or []))
            for header, key in (
                ("openai-organization", "org_id"),
                ("openai-project", "project_id"),
            ):
                if response.headers.get(header):
                    finding.account[key] = response.headers[header]
                    finding.account_source[key] = "header"
        else:
            finding = map_error(response.status_code, body)
        return observation(finding, now=ctx.now, http=trace(f"GET {path}", result), limits=limits)


PROVIDER = OpenAIProvider()
