"""Demo mode (``mecha serve --demo``): fake data for screenshots, the README and tests.

Holds **no secrets**: credentials only have redacted forms and fabricated fingerprints, and
checks never touch the network. Deterministic (fixed seed and fixed "now"), covering every
traffic-light state and every credential kind.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from mecha.core.config import Config
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
    Validity,
)
from mecha.core.service import MechaService
from mecha.core.status import Observation, evaluate
from mecha.providers.base import Provider
from mecha.store.db import Store, open_memory_store

DEMO_NOW = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)
SEED = 20261104
HISTORY_POINTS = 12

P = ProviderId
K = CredentialKind


@dataclass(frozen=True)
class Scenario:
    label: str
    provider: ProviderId
    kind: CredentialKind
    redacted: str
    endpoint: str
    validity: Validity | None = Validity.valid
    status: int = 200
    failed: bool = False
    never_checked: bool = False
    funds: Funds | None = None
    limits: Limits | None = None
    notes: tuple[Reason, ...] = ()
    error: tuple[str, str] | None = None
    account: dict[str, str] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    manual_expires_days: float | None = None
    api_expires_days: float | None = None
    token_expires_days: float | None = None
    subscription_days: float | None = None
    retiring: bool = False
    linked_path: str | None = None


def _amber(code: str) -> Reason:
    return Reason(code=code, overall=Overall.amber)


def _red(code: str) -> Reason:
    return Reason(code=code, overall=Overall.red)


def _limits(remaining: int, limit: int = 4000) -> Limits:
    state = LimitsState.throttled if remaining == 0 else LimitsState.ok
    return Limits(
        state=state,
        requests=RateWindow(
            limit=limit, remaining=remaining, reset=DEMO_NOW + timedelta(seconds=40)
        ),
    )


SCENARIOS: tuple[Scenario, ...] = (
    # --- green ---------------------------------------------------------------------
    Scenario(
        "Prod · chat backend",
        P.anthropic,
        K.api_key,
        "sk-ant-api03-Qm7…x2Pa",
        "GET /v1/models",
        limits=_limits(3987),
        account={"org_id": "org_demo_anthropic", "models": "9"},
        tags=("prod",),
    ),
    Scenario(
        "Embeddings worker",
        P.openai,
        K.api_key,
        "sk-proj-H4c…9LkQ",
        "GET /v1/models",
        limits=_limits(58, 60),
        account={"org_id": "org-demo", "models": "64"},
        tags=("prod",),
        api_expires_days=90,
    ),
    Scenario(
        "Org admin",
        P.openai,
        K.admin_key,
        "sk-admin-p0Z…T7ve",
        "GET /v1/organization/admin_api_keys",
        tags=("admin",),
    ),
    Scenario(
        "Grok team key",
        P.xai,
        K.api_key,
        "xai-Vb8…3nQs",
        "GET /v1/api-key",
        account={"team_id": "team-demo-0001", "key_name": "grok-prod"},
        tags=("prod",),
    ),
    Scenario(
        "xAI management",
        P.xai,
        K.management_key,
        "xai-Jt2…k8Wd",
        "GET /auth/management-keys/validation",
        account={"team_id": "team-demo-0001"},
        tags=("admin",),
    ),
    # --- amber ---------------------------------------------------------------------
    Scenario(
        "Legacy Gemini key",
        P.gemini,
        K.api_key,
        "AIzaSyD…Q0aM",
        "GET /v1beta/models",
        retiring=True,
        account={"models": "41"},
        tags=("legacy",),
    ),
    Scenario(
        "Demo day key",
        P.anthropic,
        K.api_key,
        "sk-ant-api03-Lr4…d9Hc",
        "GET /v1/models",
        manual_expires_days=5,
        limits=_limits(3999),
        tags=("class",),
    ),
    Scenario(
        "Batch jobs",
        P.openai,
        K.api_key,
        "sk-proj-Wq1…eZ3r",
        "GET /v1/models",
        validity=Validity.valid,
        status=429,
        limits=_limits(0, 60),
        error=("rate_limit_exceeded", "Rate limit reached for requests"),
        tags=("batch",),
    ),
    Scenario(
        "Interns project",
        P.openai,
        K.api_key,
        "sk-proj-Kc0…mN2b",
        "GET /v1/models",
        status=429,
        funds=Funds(state=FundsState.capped),
        error=("project_spend_limit_exceeded", "Your project reached its enforced spend limit."),
        tags=("class",),
    ),
    Scenario(
        "Server-only Gemini",
        P.gemini,
        K.api_key,
        "AQ.Ab8RN…f3Lz",
        "GET /v1beta/models",
        validity=None,
        status=403,
        notes=(_amber("gemini_ip_restricted"),),
        error=("PERMISSION_DENIED", "The provided API key has an IP address restriction."),
        tags=("prod",),
    ),
    # --- red -----------------------------------------------------------------------
    Scenario(
        "Old laptop key",
        P.openai,
        K.api_key,
        "sk-proj-Ds9…7Ygh",
        "GET /v1/models",
        validity=Validity.invalid,
        status=401,
        error=("invalid_api_key", "Incorrect API key provided."),
        tags=("legacy",),
    ),
    Scenario(
        "Side project",
        P.anthropic,
        K.api_key,
        "sk-ant-api03-Ze3…Ww0k",
        "GET /v1/models",
        status=402,
        funds=Funds(state=FundsState.empty),
        notes=(_red("billing_error"),),
        error=("billing_error", "There is an issue with your billing or payment information."),
    ),
    Scenario(
        "Shared hackathon",
        P.xai,
        K.api_key,
        "xai-Rt5…9pLa",
        "GET /v1/api-key",
        validity=Validity.blocked,
        notes=(_red("team_blocked"),),
        account={"team_id": "team-demo-0042"},
        tags=("class",),
    ),
    Scenario(
        "Leaked in a gist",
        P.gemini,
        K.api_key,
        "AQ.Ab8Rt…u1Xe",
        "GET /v1beta/models",
        validity=Validity.blocked,
        status=403,
        error=(
            "PERMISSION_DENIED",
            "Your API key was reported as leaked. Please use another API key.",
        ),
    ),
    Scenario(
        "Q3 contractor",
        P.openai,
        K.api_key,
        "sk-svcacct-Gm6…2Rqd",
        "GET /v1/models",
        validity=Validity.invalid,
        status=401,
        manual_expires_days=-3,
        error=("invalid_api_key", "Incorrect API key provided."),
    ),
    Scenario(
        "Prepaid Gemini",
        P.gemini,
        K.api_key,
        "AQ.Ab8Rx…c5Vn",
        "GET /v1beta/models",
        status=402,
        funds=Funds(state=FundsState.empty),
        error=("PAYMENT_REQUIRED", "Your Prepay credit balance is depleted."),
    ),
    # --- grey ----------------------------------------------------------------------
    Scenario(
        "Just added",
        P.anthropic,
        K.api_key,
        "sk-ant-api03-Nv8…a6Ft",
        "GET /v1/models",
        never_checked=True,
    ),
    Scenario(
        "Staging (offline)",
        P.xai,
        K.api_key,
        "xai-Pe4…h1Mx",
        "GET /v1/api-key",
        failed=True,
        error=("network", "timeout after 4 attempts"),
        tags=("staging",),
    ),
    # --- local only (OAuth: never sent anywhere) -----------------------------------
    Scenario(
        "Claude Max (token)",
        P.anthropic,
        K.oauth_token,
        "sk-ant-oat01-Hd2…Qz8c",
        "",
        token_expires_days=200,
    ),
    Scenario(
        "Codex CLI session",
        P.openai,
        K.oauth_file,
        "~/.codex/auth.json",
        "",
        subscription_days=24,
        linked_path="~/.codex/auth.json",
    ),
)


def _fingerprint(label: str) -> str:
    return hashlib.sha256(f"demo:{label}".encode()).hexdigest()[:12]


def _days(value: float | None) -> datetime | None:
    return DEMO_NOW + timedelta(days=value) if value is not None else None


def _observation(s: Scenario, at: datetime, latency: float, *, final: bool) -> Observation:
    trace = HttpTrace(endpoint=s.endpoint, status=s.status if final else 200, latency_ms=latency)
    if final and s.failed:
        return Observation(
            check_status=CheckStatus.failed,
            observed_at=at,
            http=HttpTrace(endpoint=s.endpoint, status=None, latency_ms=None),
            error=ProviderError(code=s.error[0], message=s.error[1]) if s.error else None,
        )
    if not final:  # earlier history: a healthy check
        return Observation(
            check_status=CheckStatus.ok,
            observed_at=at,
            validity=Validity.valid,
            http=trace,
            account=s.account,
        )
    api_expiry = _days(s.api_expires_days)
    return Observation(
        check_status=CheckStatus.ok,
        observed_at=at,
        validity=s.validity,
        funds=s.funds,
        expiry=Expiry(state=ExpiryState.scheduled, expires_at=api_expiry) if api_expiry else None,
        limits=s.limits,
        account=s.account,
        notes=list(s.notes),
        http=trace,
        error=ProviderError(code=s.error[0], message=s.error[1]) if s.error else None,
    )


def build_demo_store(scenarios: Iterable[Scenario] = SCENARIOS) -> Store:
    store = open_memory_store()
    rng = random.Random(SEED)  # noqa: S311 - fake latencies, not crypto
    for index, s in enumerate(scenarios):
        cred = Credential(
            id=f"01DEMO{index:020d}",
            label=s.label,
            provider=s.provider,
            kind=s.kind,
            pattern_id=None,
            redacted=s.redacted,
            fingerprint=_fingerprint(s.label),
            tags=list(s.tags),
            linked_path=s.linked_path,
            manual_expires_at=_days(s.manual_expires_days),
            created_at=DEMO_NOW - timedelta(days=60 - index),
            token_expires_at=_days(s.token_expires_days),
            subscription_until=_days(s.subscription_days),
            retiring=s.retiring,
        )
        store.insert(cred, None)  # no secret, ever
        if s.never_checked or cred.kind.is_oauth:
            continue
        for point in range(HISTORY_POINTS):
            at = DEMO_NOW - timedelta(hours=6 * (HISTORY_POINTS - 1 - point), minutes=index * 3)
            final = point == HISTORY_POINTS - 1
            obs = _observation(s, at, round(rng.uniform(120, 480), 1), final=final)
            store.add_result(evaluate(cred, obs, at), obs)
    return store


class DemoService(MechaService):
    """MechaService over the demo store: fixed clock, fabricated checks, no network."""

    def __init__(self, config: Config | None = None) -> None:
        store = build_demo_store()
        super().__init__(lambda: store, now=lambda: DEMO_NOW, config=config)
        self._scenarios = {f"01DEMO{i:020d}": s for i, s in enumerate(SCENARIOS)}

    # Adding works (it is what a demo video shows) but only the redacted form is kept: the
    # secret is never stored, not even in the in-memory store, and checks never use it.
    keep_secrets = False

    @staticmethod
    def _added_scenario(cred: Credential, provider: Provider) -> Scenario:
        """A credential added during the demo: a fabricated healthy check, no network."""
        _, path, _ = provider.endpoint(cred.kind)
        return Scenario(cred.label, cred.provider, cred.kind, cred.redacted, f"GET {path}")

    def is_fresh(self, cred: Credential, now: datetime) -> bool:
        return False  # every demo check "runs"

    async def _observe(self, cred: Credential, provider: Provider) -> Observation:
        scenario = self._scenarios.get(cred.id) or self._added_scenario(cred, provider)
        rng = random.Random(f"{SEED}:{cred.id}")  # noqa: S311
        return _observation(scenario, self.now(), round(rng.uniform(120, 480), 1), final=True)
