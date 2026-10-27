"""Live conversation events (P18): the browser is pushed new messages instead of re-polling the whole history.

The conversation log publishes each user-visible message (with its sequence number) here as it is
written. Interaction turns, and the agent runs the user asked for, report when work starts and stops,
which drives the browser's typing indicator. Each open browser stream subscribes with its own queue.
Publishing is safe from any thread or event loop.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple

from ...logging_config import logger

Event = Dict[str, Any]


class ConversationEvents:
    """In-process publish/subscribe for conversation events, plus whether the assistant is working."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[Tuple[asyncio.AbstractEventLoop, "asyncio.Queue[Event]"]] = []
        self._active_work = 0

    def subscribe(self) -> "asyncio.Queue[Event]":
        queue: "asyncio.Queue[Event]" = asyncio.Queue()
        with self._lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Event]") -> None:
        with self._lock:
            self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # that stream's loop has closed; it will unsubscribe on its way out
                logger.debug("dropped event for a closed stream")

    # --- Whether the assistant is working: interaction turns plus agent runs the user asked for ---

    @property
    def working(self) -> bool:
        with self._lock:
            return self._active_work > 0

    def work_started(self) -> None:
        with self._lock:
            self._active_work += 1
            first = self._active_work == 1
        if first:
            self.publish({"type": "status", "working": True})

    def work_finished(self) -> None:
        with self._lock:
            idle = self._active_work == 1  # announce only the step down to idle, never a repeat
            self._active_work = max(0, self._active_work - 1)
        if idle:
            self.publish({"type": "status", "working": False})


_events: Optional[ConversationEvents] = None
_events_lock = threading.Lock()


def get_conversation_events() -> ConversationEvents:
    global _events
    if _events is None:
        with _events_lock:
            if _events is None:
                _events = ConversationEvents()
    return _events


__all__ = ["ConversationEvents", "Event", "get_conversation_events"]
