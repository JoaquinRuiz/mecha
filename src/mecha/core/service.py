"""Service layer shared by the CLI (phase 1) and the REST API (phase 3).

Takes raw input, returns models. Never returns, logs or raises with a plaintext secret.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mecha.core.config import Config
from mecha.core.detect import Detection, detect
from mecha.core.errors import (
    AmbiguousProviderError,
    DetectionError,
    DuplicateCredentialError,
    NotFoundError,
)
from mecha.core.http import HttpSettings, make_client
from mecha.core.models import (
    CheckResult,
    CheckStatus,
    Credential,
    CredentialKind,
    Overall,
    ProviderError,
    ProviderId,
)
from mecha.core.normalize import normalize_secret
from mecha.core.status import Observation, Thresholds, evaluate
from mecha.core.ulid import new_ulid
from mecha.providers import get_provider
from mecha.providers.base import Provider, ProviderContext, remote_kinds
from mecha.store.db import Store

log = logging.getLogger(__name__)


MIN_UNKNOWN_LENGTH = 16
MIN_ID_PREFIX = 4
BACKOFF_FLOOR = timedelta(minutes=1)

_GROUP_ORDER = {
    Overall.red: 0,
    Overall.amber: 1,
    Overall.grey: 2,
    Overall.local: 3,
    Overall.green: 4,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class LastKnown:
    overall: Overall
    checked_at: datetime


@dataclass(frozen=True)
class CredentialStatus:
    credential: Credential
    result: CheckResult
    last_known: LastKnown | None = None  # last non-grey result (shown on grey cards)
    cached: bool = False  # result reused because of the minimum interval


def _clean_tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        t = tag.strip()
        if t and t not in out:
            out.append(t)
    return out


class MechaService:
    def __init__(
        self,
        open_store: Callable[[], Store],
        *,
        now: Callable[[], datetime] = _utcnow,
        config: Config | None = None,
        thresholds: Thresholds | None = None,
    ) -> None:
        self._open_store = open_store
        self._store: Store | None = None
        self._now = now
        self.config = config or Config()
        cfg_th = self.config.thresholds
        self.thresholds = thresholds or Thresholds(
            soon_days=cfg_th.soon_days, low_funds_usd=cfg_th.low_funds_usd
        )
        self._global = asyncio.Semaphore(self.config.scheduler.concurrency)
        self._per_provider: dict[ProviderId, asyncio.Semaphore] = {}

    # Demo mode keeps only the redacted form: the secret is never stored, even in memory.
    keep_secrets = True

    def now(self) -> datetime:
        return self._now()

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = self._open_store()
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # --- detect -------------------------------------------------------------------
    @staticmethod
    def detect(raw: str) -> Detection:
        """Offline classification; does not open the store."""
        return detect(raw)

    # --- add ----------------------------------------------------------------------
    def add(
        self,
        raw: str,
        *,
        label: str | None = None,
        tags: Iterable[str] = (),
        notes: str | None = None,
        provider: ProviderId | None = None,
        kind: CredentialKind | None = None,
        manual_expires_at: datetime | None = None,
    ) -> CredentialStatus:
        secret = normalize_secret(raw)
        det = detect(secret)
        resolved_provider, resolved_kind = self._resolve(det, secret, provider, kind)

        fingerprint = self.store.cipher.fingerprint(secret)
        existing = self.store.by_fingerprint(fingerprint)
        if existing is not None:
            raise DuplicateCredentialError(
                f"This credential is already stored as {existing}.", existing
            )

        jwt = det.jwt
        if manual_expires_at is not None and manual_expires_at.tzinfo is None:
            manual_expires_at = manual_expires_at.replace(tzinfo=UTC)
        cred = Credential(
            id=new_ulid(),
            label=(label or "").strip() or det.label or f"{resolved_provider.value} key",
            provider=resolved_provider,
            kind=resolved_kind,
            pattern_id=det.pattern_id,
            redacted=det.redacted,
            fingerprint=fingerprint,
            tags=_clean_tags(tags),
            notes=notes.strip() if notes and notes.strip() else None,
            manual_expires_at=manual_expires_at,
            created_at=self._now(),
            token_expires_at=jwt.expires_at if jwt else None,
            subscription_until=jwt.subscription_until if jwt else None,
            retiring=det.retiring,
        )
        self.store.insert(cred, secret if self.keep_secrets else None)
        return self._status(cred)

    @staticmethod
    def _resolve(
        det: Detection,
        secret: str,
        provider: ProviderId | None,
        kind: CredentialKind | None,
    ) -> tuple[ProviderId, CredentialKind]:
        if det.format == "session_file":
            raise DetectionError(
                f"This looks like a {det.session_tool} session file. Session files are linked "
                f"by path (`mecha link {det.session_tool}`); their tokens are never copied."
            )
        if det.format != "unknown" and not det.storable:
            raise DetectionError(
                "This looks like an OAuth refresh token. Mecha never stores refresh tokens."
            )
        if kind is CredentialKind.oauth_file:
            raise DetectionError("Session files are linked by path, not added as secrets.")

        if det.format == "unknown":
            if len(secret) < MIN_UNKNOWN_LENGTH or any(c.isspace() for c in secret):
                raise DetectionError("This does not look like a credential.")
            if provider is None:
                raise DetectionError(
                    "Unknown credential format. Pass --provider (and --kind if it is not a "
                    "plain API key, e.g. --kind management_key for xAI management keys)."
                )
            return provider, kind or CredentialKind.api_key

        if det.ambiguous:
            if provider is None:
                raise AmbiguousProviderError(
                    "The provider of this key is ambiguous; confirm it with --provider.",
                    [c.value for c in det.candidates],
                )
            resolved_provider = provider
        elif det.provider is None:  # JWT with unknown issuer
            if provider is None:
                raise DetectionError("Unknown token issuer; pass --provider.")
            resolved_provider = provider
        else:
            if provider is not None and provider != det.provider:
                raise DetectionError(
                    f"This credential looks like {det.provider.value}, not {provider.value}."
                )
            resolved_provider = det.provider

        detected_kind = det.kind or CredentialKind.api_key
        if kind is None or kind == detected_kind:
            return resolved_provider, detected_kind
        if (
            resolved_provider is ProviderId.xai
            and detected_kind is CredentialKind.api_key
            and kind is CredentialKind.management_key
        ):
            return resolved_provider, kind
        raise DetectionError(f"This credential is a {detected_kind.value}, not a {kind.value}.")

    # --- list / get / remove --------------------------------------------------------
    def _status(self, cred: Credential, *, cached: bool = False) -> CredentialStatus:
        obs = self.store.last_observation(cred.id)
        result = evaluate(cred, obs, self._now(), self.thresholds)
        known = self.store.last_known(cred.id)
        last_known = LastKnown(*known) if known else None
        return CredentialStatus(cred, result, last_known, cached)

    @staticmethod
    def _sorted(items: list[CredentialStatus]) -> list[CredentialStatus]:
        far = datetime.max.replace(tzinfo=UTC)

        def key(item: CredentialStatus) -> tuple[int, datetime, str]:
            expires = item.result.expiry.value.expires_at or far
            return (_GROUP_ORDER[item.result.overall], expires, item.credential.label.lower())

        return sorted(items, key=key)

    def list(self) -> list[CredentialStatus]:
        return self._sorted([self._status(c) for c in self.store.all()])

    # --- check ----------------------------------------------------------------------
    def _settings(self) -> HttpSettings:
        h = self.config.http
        return HttpSettings(h.timeout_seconds, h.retries, h.backoff_seconds)

    def _semaphore(self, provider: ProviderId) -> asyncio.Semaphore:
        if provider not in self._per_provider:
            self._per_provider[provider] = asyncio.Semaphore(self.config.scheduler.per_provider)
        return self._per_provider[provider]

    def is_remote(self, cred: Credential) -> bool:
        return cred.kind in remote_kinds() and get_provider(cred.provider) is not None

    def is_fresh(self, cred: Credential, now: datetime) -> bool:
        """A successful observation younger than the minimum interval (failures never count)."""
        obs = self.store.last_observation(cred.id)
        window = timedelta(minutes=self.config.scheduler.min_interval_minutes)
        return (
            obs is not None
            and obs.check_status is CheckStatus.ok
            and now - obs.observed_at < window
        )

    def retry_delay(self, failures: int) -> timedelta:
        """Backoff after ``failures`` failed checks in a row: min_interval, 2x, 4x… capped
        at interval_hours. Without failures, the regular interval."""
        sched = self.config.scheduler
        interval = timedelta(hours=sched.interval_hours)
        if failures <= 0:
            return interval
        # Floor of 1 minute: min_interval_minutes may be 0 (no caching), which must not
        # turn a failure into a retry on every scheduler tick.
        base = max(timedelta(minutes=sched.min_interval_minutes), BACKOFF_FLOOR)
        delay: timedelta = base * (2 ** min(failures - 1, 30))
        return min(delay, interval)

    def next_due(self, cred: Credential) -> datetime | None:
        """When the scheduler should check ``cred`` next (None: now)."""
        obs = self.store.last_observation(cred.id)
        if obs is None:
            return None
        failures = (
            self.store.failure_streak(cred.id) if obs.check_status is not CheckStatus.ok else 0
        )
        return obs.observed_at + self.retry_delay(failures)

    async def _observe(self, cred: Credential, provider: Provider) -> Observation:
        now = self._now()
        ctx = ProviderContext(
            now=now,
            http=self._settings(),
            anthropic_version=self.config.providers.anthropic.version,
        )
        secret = self.store.secret(cred.id)
        if secret is None:  # pragma: no cover - only linked files have no secret (phase 4)
            return Observation(check_status=CheckStatus.failed, observed_at=now)
        try:
            async with (
                self._global,
                self._semaphore(cred.provider),
                make_client(provider.allowed_hosts, self._settings()) as http,
            ):
                return await provider.check_passive(cred, secret, http, ctx)
        except Exception as exc:  # a plugin bug must not take the whole batch down
            log.warning("provider %s failed: %s", cred.provider.value, type(exc).__name__)
            log.debug("provider failure", exc_info=True)
            return Observation(
                check_status=CheckStatus.failed,
                observed_at=now,
                error=ProviderError(code="internal", message=type(exc).__name__),
            )
        finally:
            del secret

    async def _check_one(self, cred: Credential, force: bool) -> CredentialStatus:
        provider = get_provider(cred.provider)
        if provider is None or not self.is_remote(cred):
            return self._status(cred)  # OAuth / linked: offline only, never a remote call
        if not force and self.is_fresh(cred, self._now()):
            return self._status(cred, cached=True)
        obs = await self._observe(cred, provider)
        result = evaluate(cred, obs, self._now(), self.thresholds)
        self.store.add_result(result, obs)
        return self._status(cred)

    async def check(
        self,
        ids: Sequence[str] = (),
        *,
        force: bool = False,
        on_result: Callable[[CredentialStatus, int, int], None] | None = None,
    ) -> builtins.list[CredentialStatus]:
        """Passive check of ``ids`` (all credentials if empty), respecting concurrency.

        ``on_result(item, done, total)`` is called as each credential finishes.
        """
        creds = [self.get(i).credential for i in ids] if ids else self.store.all()
        total, done = len(creds), 0

        async def one(cred: Credential) -> CredentialStatus:
            nonlocal done
            item = await self._check_one(cred, force)
            done += 1
            if on_result is not None:
                on_result(item, done, total)
            return item

        items = await asyncio.gather(*(one(c) for c in creds))
        return self._sorted(list(items))

    def check_sync(
        self, ids: Sequence[str] = (), *, force: bool = False
    ) -> builtins.list[CredentialStatus]:
        return asyncio.run(self.check(ids, force=force))

    def resolve_id(self, id_or_prefix: str) -> str:
        value = id_or_prefix.strip().upper()
        if self.store.get(value) is not None:
            return value
        if len(value) < MIN_ID_PREFIX:
            raise NotFoundError(f"No credential {id_or_prefix!r} (use at least 4 characters).")
        matches = self.store.find_ids(value)
        if not matches:
            raise NotFoundError(f"No credential matches {id_or_prefix!r}.")
        if len(matches) > 1:
            raise NotFoundError(
                f"{id_or_prefix!r} matches {len(matches)} credentials; be more specific."
            )
        return matches[0]

    def get(self, id_or_prefix: str) -> CredentialStatus:
        cred = self.store.get(self.resolve_id(id_or_prefix))
        if cred is None:  # pragma: no cover - resolve_id guarantees existence
            raise NotFoundError(f"No credential {id_or_prefix!r}.")
        return self._status(cred)

    def update(
        self,
        id_or_prefix: str,
        *,
        label: str | None = None,
        tags: Iterable[str] | None = None,
        notes: str | None = None,
        manual_expires_at: datetime | None = None,
        clear: Iterable[str] = (),
    ) -> CredentialStatus:
        """Edit non-secret fields. ``clear`` names fields to reset (notes, manual_expires_at)."""
        cred_id = self.resolve_id(id_or_prefix)
        changes: dict[str, object] = {}
        if label is not None:
            if not label.strip():
                raise DetectionError("The label cannot be empty.")
            changes["label"] = label.strip()
        if tags is not None:
            changes["tags"] = _clean_tags(tags)
        if notes is not None:
            changes["notes"] = notes.strip() or None
        if manual_expires_at is not None:
            if manual_expires_at.tzinfo is None:
                manual_expires_at = manual_expires_at.replace(tzinfo=UTC)
            changes["manual_expires_at"] = manual_expires_at
        for field_name in clear:
            if field_name not in ("notes", "manual_expires_at"):
                raise DetectionError(f"{field_name} cannot be cleared.")
            changes[field_name] = None
        self.store.update(cred_id, changes)
        return self.get(cred_id)

    def history(self, id_or_prefix: str, limit: int = 50) -> builtins.list[CheckResult]:
        return self.store.history(self.resolve_id(id_or_prefix), limit)

    def remove(self, id_or_prefix: str) -> Credential:
        cred = self.get(id_or_prefix).credential
        self.store.delete(cred.id)
        return cred
