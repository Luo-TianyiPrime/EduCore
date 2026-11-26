"""In-process task event fan-out backed by SQLite."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.storage.tasks import TaskRepo

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, repo: TaskRepo) -> None:
        self.repo = repo
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def publish(self, task_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.repo.add_event(task_id, event_type, payload)
        for queue in list(self._subscribers.get(task_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:  # pragma: no cover - defensive
                    pass
        return event

    def subscribe(self, task_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(task_id, set()).add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(task_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)