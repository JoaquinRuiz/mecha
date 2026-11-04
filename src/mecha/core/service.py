"""Service layer shared by the CLI (phase 1) and the REST API (phase 3).

Takes raw input, returns models. Never returns, logs or raises with a plaintext secret.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from mecha.core.detect import Detection, detect
from mecha.core.errors import (
    AmbiguousProviderError,
    DetectionError,
    DuplicateCredentialError,
    NotFoundError,
)
from mecha.core.models import CheckResult, Credential, CredentialKind, Overall, ProviderId
from mecha.core.normalize import normalize_secret
from mecha.core.status import Thresholds, evaluate_offline
from mecha.core.ulid import new_ulid
from mecha.store.db import Store

MIN_UNKNOWN_LENGTH = 16
MIN_ID_PREFIX = 4

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
class CredentialStatus:
    credential: Credential
    result: CheckResult


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
        thresholds: Thresholds | None = None,
    ) -> None:
        self._open_store = open_store
        self._store: Store | None = None
        self._now = now
        self.thresholds = thresholds or Thresholds()

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
        self.store.insert(cred, secret)
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
    def _status(self, cred: Credential) -> CredentialStatus:
        return CredentialStatus(cred, evaluate_offline(cred, self._now(), self.thresholds))

    def list(self) -> list[CredentialStatus]:
        items = [self._status(c) for c in self.store.all()]
        far = datetime.max.replace(tzinfo=UTC)

        def key(item: CredentialStatus) -> tuple[int, datetime, str]:
            expires = item.result.expiry.value.expires_at or far
            return (_GROUP_ORDER[item.result.overall], expires, item.credential.label.lower())

        return sorted(items, key=key)

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

    def remove(self, id_or_prefix: str) -> Credential:
        cred = self.get(id_or_prefix).credential
        self.store.delete(cred.id)
        return cred
