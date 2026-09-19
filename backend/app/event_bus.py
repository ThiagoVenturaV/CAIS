from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from .database import Database
from .domain import DomainEvent


logger = logging.getLogger(__name__)
EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventBus:
    """Small in-process bus for the MVP, backed by a durable SQLite journal."""

    def __init__(self, database: Database):
        self.database = database
        self.queue: asyncio.Queue[DomainEvent] = asyncio.Queue()
        self.handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self.running = False
        self.last_error: str | None = None

    def subscribe_handler(self, event_type: str, handler: EventHandler) -> None:
        self.handlers[event_type].append(handler)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run(), name="cais-event-bus")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def publish(self, event: DomainEvent) -> None:
        self.database.append_journal(event.as_dict())
        await self.queue.put(event)
        await self._broadcast(event)

    async def _run(self) -> None:
        while self.running:
            event = await self.queue.get()
            try:
                for handler in self.handlers.get(event.event_type, []):
                    await handler(event)
                self.last_error = None
            except Exception as exc:  # domain failure is observable, bus must stay alive
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("event_handler_failed", extra={"event_type": event.event_type})
            finally:
                self.queue.task_done()

    async def _broadcast(self, event: DomainEvent) -> None:
        stale: list[asyncio.Queue[dict[str, Any]]] = []
        message = event.as_dict()
        for subscriber in self.subscribers:
            try:
                subscriber.put_nowait(message)
            except asyncio.QueueFull:
                stale.append(subscriber)
        for subscriber in stale:
            self.subscribers.discard(subscriber)

    def add_subscriber(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)
