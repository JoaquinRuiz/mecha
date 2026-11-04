"""Truth table of the traffic light (SPEC.md, "Reglas del semáforo")."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from mecha.core.models import (
    CheckStatus,
    Credential,
    CredentialKind,
    ExpiryState,
    FundsState,
    LimitsState,
    Overall,
    ProviderId,
    Reason,
    Validity,
)
from mecha.core.status import (
    Thresholds,
    compute_overall,
    evaluate_offline,
    exit_code_for,
    expiry_state,
    worst,
)

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


NOTES = {
    "none": (),
    "amber": (Reason(code="permission_denied", overall=Overall.amber),),
    "red": (Reason(code="billing_error", overall=Overall.red),),
}


def oracle(
    kind: CredentialKind,
    validity: Validity,
    funds: FundsState,
    expiry: ExpiryState,
    limits: LimitsState | None,
    retiring: bool,
    check: CheckStatus,
    notes: str,
) -> Overall:
    """Independent statement of the spec rules, in priority order."""
    is_red = (
        validity in (Validity.invalid, Validity.revoked, Validity.blocked)
        or funds == FundsState.empty
        or expiry == ExpiryState.expired
        or notes == "red"
    )
    is_amber = (
        expiry == ExpiryState.soon
        or funds in (FundsState.low, FundsState.capped)
        or limits == LimitsState.throttled
        or retiring
        or notes == "amber"
    )
    if is_red:
        return Overall.red
    if is_amber:
        return Overall.amber
    if kind in (CredentialKind.oauth_token, CredentialKind.oauth_file):
        return Overall.local
    if check != CheckStatus.ok or validity != Validity.valid:
        return Overall.grey
    return Overall.green


SEVERITY = {Overall.green: 0, Overall.local: 0, Overall.grey: 1, Overall.amber: 2, Overall.red: 3}

ROWS = list(
    itertools.product(
        list(CredentialKind),
        list(Validity),
        list(FundsState),
        list(ExpiryState),
        [None, *LimitsState],
        [False, True],
        list(CheckStatus),
        list(NOTES),
    )
)


def test_truth_table_is_complete() -> None:
    assert len(ROWS) == 5 * 6 * 6 * 5 * 4 * 2 * 3 * 3


@pytest.mark.parametrize("chunk", range(10))
def test_truth_table(chunk: int) -> None:
    for row in ROWS[chunk::10]:
        kind, validity, funds, expiry, limits, retiring, check, notes = row
        overall, reasons = compute_overall(
            kind=kind,
            validity=validity,
            funds=funds,
            expiry=expiry,
            limits=limits,
            retiring=retiring,
            check_status=check,
            notes=NOTES[notes],
        )
        assert overall == oracle(*row), row
        assert reasons, row
        # At least one reason explains the colour; red cards also list their amber reasons.
        assert any(r.overall == overall for r in reasons), row
        assert all(SEVERITY[r.overall] <= SEVERITY[overall] for r in reasons), row
        got = {r.code for r in reasons}
        # A 401 on a key whose known expiry passed reads as expired, not invalid.
        if validity is Validity.invalid and expiry is ExpiryState.expired:
            assert "validity_invalid" not in got, row
            assert "expired" in got, row
        # "billing problem" replaces "no funds" rather than adding to it.
        if notes == "red" and funds is FundsState.empty:
            assert "funds_empty" not in got, row


def test_grey_reasons_distinguish_not_checked_from_failed() -> None:
    args = {
        "kind": CredentialKind.api_key,
        "validity": Validity.unknown,
        "funds": FundsState.unknown,
        "expiry": ExpiryState.unknown,
        "limits": None,
        "retiring": False,
    }
    _, not_checked = compute_overall(**args, check_status=CheckStatus.not_checked)  # type: ignore[arg-type]
    _, failed = compute_overall(**args, check_status=CheckStatus.failed)  # type: ignore[arg-type]
    assert [r.code for r in not_checked] == ["not_checked"]
    assert [r.code for r in failed] == ["check_failed"]


def test_offline_conditions_beat_failed_check() -> None:
    # A retiring AIza key with a network failure is amber, not grey.
    overall, _ = compute_overall(
        kind=CredentialKind.api_key,
        validity=Validity.unknown,
        funds=FundsState.unknown,
        expiry=ExpiryState.unknown,
        limits=None,
        retiring=True,
        check_status=CheckStatus.failed,
    )
    assert overall is Overall.amber
    # A known past expiry is red even if the check failed.
    overall, _ = compute_overall(
        kind=CredentialKind.api_key,
        validity=Validity.unknown,
        funds=FundsState.unknown,
        expiry=ExpiryState.expired,
        limits=None,
        retiring=False,
        check_status=CheckStatus.failed,
    )
    assert overall is Overall.red


@pytest.mark.parametrize(
    ("delta", "state"),
    [
        (None, ExpiryState.unknown),
        (timedelta(days=-1), ExpiryState.expired),
        (timedelta(0), ExpiryState.expired),
        (timedelta(days=13, hours=23), ExpiryState.soon),
        (timedelta(days=14), ExpiryState.scheduled),
        (timedelta(days=90), ExpiryState.scheduled),
    ],
)
def test_expiry_state(delta: timedelta | None, state: ExpiryState) -> None:
    expires = NOW + delta if delta is not None else None
    assert expiry_state(expires, NOW, Thresholds()) is state


def _cred(**kw: object) -> Credential:
    base: dict[str, object] = {
        "id": "01TEST",
        "label": "t",
        "provider": ProviderId.anthropic,
        "kind": CredentialKind.api_key,
        "redacted": "x…",
        "fingerprint": "f" * 12,
        "created_at": NOW,
    }
    base.update(kw)
    return Credential.model_validate(base)


def test_offline_plain_key_is_grey_not_checked() -> None:
    r = evaluate_offline(_cred(), NOW)
    assert r.overall is Overall.grey
    assert r.mode == "offline"
    assert r.check_status is CheckStatus.not_checked
    assert [x.code for x in r.reasons] == ["not_checked"]


def test_offline_retiring_is_amber() -> None:
    r = evaluate_offline(_cred(provider=ProviderId.gemini, retiring=True), NOW)
    assert r.overall is Overall.amber


def test_offline_manual_expiry() -> None:
    r = evaluate_offline(_cred(manual_expires_at=NOW + timedelta(days=3)), NOW)
    assert r.overall is Overall.amber
    assert r.expiry.source == "manual"
    r = evaluate_offline(_cred(manual_expires_at=NOW - timedelta(days=1)), NOW)
    assert r.overall is Overall.red


def test_offline_oauth_is_local_and_uses_earliest_jwt_date() -> None:
    cred = _cred(
        kind=CredentialKind.oauth_token,
        token_expires_at=NOW + timedelta(days=60),
        subscription_until=NOW + timedelta(days=30),
    )
    r = evaluate_offline(cred, NOW)
    assert r.overall is Overall.local
    assert r.validity.value is Validity.not_applicable
    assert r.expiry.value.expires_at == NOW + timedelta(days=30)
    assert r.expiry.source == "jwt"


def test_offline_oauth_expiry_rules_still_apply() -> None:
    soon = _cred(kind=CredentialKind.oauth_token, token_expires_at=NOW + timedelta(days=2))
    assert evaluate_offline(soon, NOW).overall is Overall.amber
    gone = _cred(kind=CredentialKind.oauth_token, token_expires_at=NOW - timedelta(hours=1))
    assert evaluate_offline(gone, NOW).overall is Overall.red


def test_manual_date_wins_when_earlier() -> None:
    cred = _cred(
        kind=CredentialKind.oauth_token,
        token_expires_at=NOW + timedelta(days=60),
        manual_expires_at=NOW + timedelta(days=20),
    )
    assert evaluate_offline(cred, NOW).expiry.source == "manual"


@pytest.mark.parametrize(
    ("overalls", "code"),
    [
        ([], 0),
        ([Overall.green, Overall.grey, Overall.local], 0),
        ([Overall.green, Overall.amber], 1),
        ([Overall.amber, Overall.red, Overall.grey], 2),
    ],
)
def test_exit_codes(overalls: list[Overall], code: int) -> None:
    assert exit_code_for(overalls) == code


def test_worst() -> None:
    assert worst([Overall.green, Overall.local, Overall.grey]) is Overall.grey
    assert worst([]) is None


def test_api_expiry_keeps_ticking_between_checks() -> None:
    from mecha.core.models import Expiry
    from mecha.core.status import Observation, evaluate

    obs = Observation(
        check_status=CheckStatus.ok,
        observed_at=NOW,
        validity=Validity.valid,
        expiry=Expiry(state=ExpiryState.scheduled, expires_at=NOW + timedelta(days=20)),
    )
    cred = _cred()
    assert evaluate(cred, obs, NOW).overall is Overall.green
    later = evaluate(cred, obs, NOW + timedelta(days=10))
    assert later.overall is Overall.amber
    assert later.expiry.source == "api"
    gone = evaluate(cred, obs, NOW + timedelta(days=21))
    assert gone.overall is Overall.red


def test_failed_check_keeps_offline_expiry_but_drops_remote_data() -> None:
    from mecha.core.status import Observation, evaluate

    obs = Observation(check_status=CheckStatus.failed, observed_at=NOW)
    r = evaluate(_cred(manual_expires_at=NOW + timedelta(days=2)), obs, NOW)
    assert r.overall is Overall.amber
    assert r.validity.value is Validity.unknown
    assert r.account == {}
