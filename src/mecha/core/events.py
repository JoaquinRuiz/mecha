"""In-process event bus for progress events (served as a stream by ``GET /api/events``).

Events only tell the UI *what changed* so it can refetch over REST; they never carry
credential data. Closing the bus ends every open stream, so shutdown never hangs on them.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

_CLOSE = object()


class EventBus:
    def __init__(self, max_queue: int = 256) -> None:
        self._subscribers: set[asyncio.Queue[Any]] = set()
        self._max_queue = max_queue
        self.closed = False

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):  # a stalled client just misses events
                queue.put_nowait(event)

    def close(self) -> None:
        self.closed = True
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(_CLOSE)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    async def subscribe(self, keepalive: float = 15.0) -> AsyncIterator[dict[str, Any] | None]:
        """Yield events; ``None`` means "send a keepalive". Ends when the bus closes."""
        queue: asyncio.Queue[Any] = asyncio.Queue(self._max_queue)
        self._subscribers.add(queue)
        try:
            while not self.closed:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=keepalive)
                except TimeoutError:
                    yield None
                    continue
                if item is _CLOSE:
                    return
                yield item
        finally:
            self._subscribers.discard(queue)
