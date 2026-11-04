"""Periodic passive checks inside ``mecha serve`` (a plain asyncio task, no extra deps).

Every ``tick`` seconds it checks the credentials whose last observation is older than
``interval_hours``. Concurrency and the per-credential minimum interval are enforced by
:meth:`MechaService.check`. Only free passive endpoints are used.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from mecha.core.service import CredentialStatus, MechaService

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, service: MechaService, *, tick_seconds: float = 60.0) -> None:
        self.service = service
        self.tick_seconds = tick_seconds
        self.interval = timedelta(hours=service.config.scheduler.interval_hours)

    def due(self, now: datetime) -> list[str]:
        out = []
        for cred in self.service.store.all():
            if not self.service.is_remote(cred):
                continue
            obs = self.service.store.last_observation(cred.id)
            if obs is None or now - obs.observed_at >= self.interval:
                out.append(cred.id)
        return out

    async def run_once(self) -> list[CredentialStatus]:
        ids = self.due(self.service.now())
        if not ids:
            return []
        log.info("scheduled passive check of %d credential(s)", len(ids))
        return await self.service.check(ids)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # keep the scheduler alive; the next tick retries
                log.exception("scheduled check failed")
            await asyncio.sleep(self.tick_seconds)
