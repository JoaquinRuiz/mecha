"""Domain models (SPEC.md, "Modelo de estados" and "Modelo de datos y API REST").

No model here ever carries a plaintext secret.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderId(StrEnum):
    anthropic = "anthropic"
    openai = "openai"
    gemini = "gemini"
    xai = "xai"
    openrouter = "openrouter"


class CredentialKind(StrEnum):
    api_key = "api_key"
    admin_key = "admin_key"
    management_key = "management_key"
    oauth_token = "oauth_token"  # noqa: S105 - an enum value
    oauth_file = "oauth_file"

    @property
    def is_oauth(self) -> bool:
        return self in (CredentialKind.oauth_token, CredentialKind.oauth_file)


class Validity(StrEnum):
    valid = "valid"
    invalid = "invalid"
    revoked = "revoked"
    blocked = "blocked"
    not_applicable = "not_applicable"
    unknown = "unknown"


class FundsState(StrEnum):
    ok = "ok"
    low = "low"
    empty = "empty"
    capped = "capped"
    not_applicable = "not_applicable"
    unknown = "unknown"


class ExpiryState(StrEnum):
    none = "none"
    scheduled = "scheduled"
    soon = "soon"
    expired = "expired"
    unknown = "unknown"


class LimitsState(StrEnum):
    ok = "ok"
    throttled = "throttled"
    unknown = "unknown"


class Overall(StrEnum):
    green = "green"
    amber = "amber"
    red = "red"
    grey = "grey"
    local = "local"


class CheckStatus(StrEnum):
    """Whether a remote check produced data. Distinguishes «sin comprobar» from «falló»."""

    not_checked = "not_checked"
    failed = "failed"
    ok = "ok"


Source = Literal["api", "header", "jwt", "file", "manual", "inferred"]
Mode = Literal["offline", "passive", "active", "enrich"]


class Signal[T](BaseModel):
    value: T
    source: Source
    observed_at: datetime


class Funds(BaseModel):
    state: FundsState
    amount: Decimal | None = None
    currency: str | None = None


class Expiry(BaseModel):
    state: ExpiryState
    expires_at: datetime | None = None


class RateWindow(BaseModel):
    limit: int | None = None
    remaining: int | None = None
    reset: datetime | None = None


class Limits(BaseModel):
    state: LimitsState
    requests: RateWindow | None = None
    tokens: RateWindow | None = None


class Budget(BaseModel):
    amount: Decimal
    currency: str = "USD"
    purchased_at: datetime


class HttpTrace(BaseModel):
    endpoint: str
    status: int | None
    latency_ms: float | None
    headers: dict[str, str] = Field(default_factory=dict)  # already redacted


class ProviderError(BaseModel):
    code: str
    message: str


class Reason(BaseModel):
    """Why the overall colour is what it is; ``code`` is translated by the UI/CLI."""

    code: str
    overall: Overall


class Credential(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    provider: ProviderId
    kind: CredentialKind
    pattern_id: str | None = None
    redacted: str
    fingerprint: str
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    linked_path: str | None = None
    parent_id: str | None = None
    manual_expires_at: datetime | None = None
    manual_budget: Budget | None = None
    active_probe: bool = False
    created_at: datetime
    # Non-secret facts derived offline at add time (JWT claims, retiring key type…).
    token_expires_at: datetime | None = None
    subscription_until: datetime | None = None
    retiring: bool = False


class CheckResult(BaseModel):
    credential_id: str
    checked_at: datetime
    mode: Mode
    check_status: CheckStatus
    overall: Overall
    reasons: list[Reason] = Field(default_factory=list)
    validity: Signal[Validity]
    funds: Signal[Funds]
    expiry: Signal[Expiry]
    limits: Signal[Limits] | None = None
    account: dict[str, Signal[str]] = Field(default_factory=dict)
    http: HttpTrace | None = None
    error: ProviderError | None = None
