"""State engine (SPEC.md, "Reglas del semáforo"). Pure functions, no I/O.

Priority: red > amber > grey > green. ``local`` is the neutral state of OAuth credentials
(validity ``not_applicable``): it applies only when nothing is red or amber and never mixes
with grey. Offline conditions (retiring key type, known expiry dates) are always evaluated,
even when the remote check could not run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from mecha.core.models import (
    CheckResult,
    CheckStatus,
    Credential,
    CredentialKind,
    Expiry,
    ExpiryState,
    Funds,
    FundsState,
    LimitsState,
    Overall,
    Reason,
    Signal,
    Source,
    Validity,
)

RED_VALIDITY = {Validity.invalid, Validity.revoked, Validity.blocked}

# Reason codes (translated by CLI/UI).
R_NOT_CHECKED = "not_checked"  # «sin comprobar»
R_CHECK_FAILED = "check_failed"  # «falló la comprobación»
R_VALIDITY_UNKNOWN = "validity_unknown"
R_LOCAL_ONLY = "local_only"
R_VALID = "valid"


@dataclass(frozen=True)
class Thresholds:
    soon_days: int = 14
    low_funds_usd: Decimal = Decimal("5")


def expiry_state(expires_at: datetime | None, now: datetime, th: Thresholds) -> ExpiryState:
    if expires_at is None:
        return ExpiryState.unknown
    if expires_at <= now:
        return ExpiryState.expired
    if expires_at - now < timedelta(days=th.soon_days):
        return ExpiryState.soon
    return ExpiryState.scheduled


def compute_overall(
    *,
    kind: CredentialKind,
    validity: Validity,
    funds: FundsState,
    expiry: ExpiryState,
    limits: LimitsState | None,
    retiring: bool,
    check_status: CheckStatus,
) -> tuple[Overall, list[Reason]]:
    red: list[str] = []
    if validity in RED_VALIDITY:
        red.append(f"validity_{validity.value}")
    if funds is FundsState.empty:
        red.append("funds_empty")
    if expiry is ExpiryState.expired:
        red.append("expired")

    amber: list[str] = []
    if expiry is ExpiryState.soon:
        amber.append("expires_soon")
    if funds is FundsState.low:
        amber.append("funds_low")
    if funds is FundsState.capped:
        amber.append("funds_capped")
    if limits is LimitsState.throttled:
        amber.append("throttled")
    if retiring:
        amber.append("retiring_key_type")

    reasons = [Reason(code=c, overall=Overall.red) for c in red]
    reasons += [Reason(code=c, overall=Overall.amber) for c in amber]
    if red:
        return Overall.red, reasons
    if amber:
        return Overall.amber, reasons

    if kind.is_oauth:
        return Overall.local, [Reason(code=R_LOCAL_ONLY, overall=Overall.local)]

    if check_status is CheckStatus.not_checked:
        return Overall.grey, [Reason(code=R_NOT_CHECKED, overall=Overall.grey)]
    if check_status is CheckStatus.failed:
        return Overall.grey, [Reason(code=R_CHECK_FAILED, overall=Overall.grey)]
    if validity is Validity.valid:
        return Overall.green, [Reason(code=R_VALID, overall=Overall.green)]
    return Overall.grey, [Reason(code=R_VALIDITY_UNKNOWN, overall=Overall.grey)]


def known_expiry(cred: Credential) -> tuple[datetime | None, Source]:
    """Earliest expiry known offline: manual date, JWT ``exp`` or subscription end."""
    candidates: list[tuple[datetime, Source]] = []
    if cred.manual_expires_at is not None:
        candidates.append((cred.manual_expires_at, "manual"))
    if cred.token_expires_at is not None:
        candidates.append((cred.token_expires_at, "jwt"))
    if cred.subscription_until is not None:
        candidates.append((cred.subscription_until, "jwt"))
    if not candidates:
        return None, "inferred"
    return min(candidates, key=lambda c: c[0])


def evaluate_offline(cred: Credential, now: datetime, th: Thresholds | None = None) -> CheckResult:
    th = th or Thresholds()
    oauth = cred.kind.is_oauth
    validity = Validity.not_applicable if oauth else Validity.unknown
    funds = FundsState.not_applicable if oauth else FundsState.unknown
    expires_at, expiry_source = known_expiry(cred)
    expiry = expiry_state(expires_at, now, th)

    overall, reasons = compute_overall(
        kind=cred.kind,
        validity=validity,
        funds=funds,
        expiry=expiry,
        limits=None,
        retiring=cred.retiring,
        check_status=CheckStatus.not_checked,
    )
    return CheckResult(
        credential_id=cred.id,
        checked_at=now,
        mode="offline",
        check_status=CheckStatus.not_checked,
        overall=overall,
        reasons=reasons,
        validity=Signal[Validity](value=validity, source="inferred", observed_at=now),
        funds=Signal[Funds](value=Funds(state=funds), source="inferred", observed_at=now),
        expiry=Signal[Expiry](
            value=Expiry(state=expiry, expires_at=expires_at),
            source=expiry_source,
            observed_at=now,
        ),
        limits=None,
    )


def worst(overalls: list[Overall]) -> Overall | None:
    order = [Overall.red, Overall.amber, Overall.grey, Overall.local, Overall.green]
    return next((o for o in order if o in overalls), None)


def exit_code_for(overalls: list[Overall]) -> int:
    """CLI contract: 0 green/grey/local, 1 any amber, 2 any red (3 = Mecha failure)."""
    if Overall.red in overalls:
        return 2
    if Overall.amber in overalls:
        return 1
    return 0
