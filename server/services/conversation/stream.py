"""The browser's live view of the conversation, as Server-Sent Events (P18).

A stream first replays what the client hasn't seen (messages after the sequence number it last
saw, so a reconnect never loses anything), then the assistant's working state, then live events:

    id: 12                 event: message   data: {"seq": 12, "role": "assistant", "content": "...", ...}
                           event: status    data: {"working": true}
    id: 0                  event: cleared   data: {}

A comment line every `event_stream_heartbeat_seconds` keeps proxies from closing an idle stream.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

from ...config import get_settings
from .events import get_conversation_events
from .log import get_conversation_log


def _sse(event: str, data: Dict[str, Any], event_id: Any = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _message(seq: int, role: str, content: str, timestamp: Any) -> str:
    return _sse("message", {"seq": seq, "role": role, "content": content, "timestamp": timestamp}, seq)


async def conversation_event_stream(after: int, is_disconnected: Callable[[], Awaitable[bool]]) -> AsyncIterator[str]:
    events = get_conversation_events()
    log = get_conversation_log()
    queue = events.subscribe()  # before reading the backlog, so nothing written in between is missed
    try:
        yield "retry: 3000\n\n"  # how long the browser waits before reconnecting
        if after > log.entry_count():  # the history was cleared while this client was away
            yield _sse("cleared", {}, 0)
            after = 0
        last = after
        for message in log.to_chat_messages(after=after):
            yield _message(message.seq, message.role, message.content, message.timestamp)
            last = message.seq
        yield _sse("status", {"working": events.working})

        heartbeat = get_settings().event_stream_heartbeat_seconds
        while not await is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event["type"] == "message":
                if event["seq"] > last:  # anything older was already in the backlog
                    last = event["seq"]
                    yield _message(event["seq"], event["role"], event["content"], event["timestamp"])
            elif event["type"] == "cleared":
                last = 0
                yield _sse("cleared", {}, 0)
            elif event["type"] == "status":
                yield _sse("status", {"working": event["working"]})
    finally:
        events.unsubscribe(queue)


__all__ = ["conversation_event_stream"]
