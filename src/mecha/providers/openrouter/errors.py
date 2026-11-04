"""OpenRouter mapping (phase 4 wires the plugin). Pure functions.

Docs: https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from mecha.core.models import ExpiryState, FundsState, Validity
from mecha.providers.base import Finding


def map_key_info(body: dict[str, Any], now: datetime) -> Finding:
    inner = body.get("data")
    data: dict[str, Any] = inner if isinstance(inner, dict) else body
    finding = Finding(validity=Validity.valid)
    limit, remaining = data.get("limit"), data.get("limit_remaining")
    if limit is None:
        finding.funds = FundsState.not_applicable  # no credit limit on this key
    elif remaining is not None:
        try:
            amount = Decimal(str(remaining))
        except InvalidOperation:
            amount = None
        if amount is not None:
            finding.funds = FundsState.empty if amount <= 0 else FundsState.ok
            finding.funds_amount, finding.funds_currency = amount, "USD"
    expires = data.get("expires_at")
    if isinstance(expires, str) and expires:
        at = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        finding.expires_at = at
        finding.expiry = ExpiryState.expired if at <= now else ExpiryState.scheduled
    elif "expires_at" in data and expires is None:
        finding.expiry = ExpiryState.none
    if data.get("is_free_tier") is True:
        finding.account["plan"] = "free_tier"
    return finding
