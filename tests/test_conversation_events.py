"""Tests for live conversation events (P18): sequence numbers, the event hub, the SSE stream, working state."""

import asyncio
import json

import pytest

from fakes import calls, reply
from server.config import Settings
from server.services import get_conversation_log
from server.services.conversation import stream as stream_module
from server.services.conversation.events import ConversationEvents, get_conversation_events
from server.services.conversation.log import ConversationLog
from server.services.conversation.stream import conversation_event_stream


def drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def settle():
    for _ in range(3):  # publish hands events over with call_soon_threadsafe
        await asyncio.sleep(0)


# --- The event hub ----------------------------------------------------------------------

async def test_subscribers_get_every_event_until_they_leave():
    hub = ConversationEvents()
    first, second = hub.subscribe(), hub.subscribe()
    hub.publish({"type": "message", "seq": 1})
    await settle()
    assert drain(first) == drain(second) == [{"type": "message", "seq": 1}]
    hub.unsubscribe(first)
    hub.publish({"type": "cleared"})
    await settle()
    assert (drain(first), drain(second), hub.subscriber_count) == ([], [{"type": "cleared"}], 1)


async def test_working_state_changes_only_at_the_edges():
    hub = ConversationEvents()
    queue = hub.subscribe()
    hub.work_started()
    hub.work_started()  # a second overlapping piece of work: still just "working"
    hub.work_finished()
    assert hub.working is True
    hub.work_finished()
    hub.work_finished()  # an extra finish never goes negative
    await settle()
    assert drain(queue) == [{"type": "status", "working": True}, {"type": "status", "working": False}]
    assert hub.working is False


def test_publishing_with_no_subscribers_is_harmless():
    ConversationEvents().publish({"type": "cleared"})


# --- Sequence numbers in the conversation log ------------------------------------------------

@pytest.fixture
def conversation(tmp_path):
    log = ConversationLog(tmp_path / "conversation.log")
    # A private, do-nothing mirror, so these tests never touch the shared working-memory log
    log._working_memory_log = type("Mirror", (), {"append_entry": lambda *a, **k: None, "clear": lambda self: None})()
    return log


def test_every_entry_has_a_sequence_number_and_only_chat_entries_are_shown(conversation):
    conversation.record_user_message("hi")          # 1
    conversation.record_agent_message("internal")   # 2 (never shown)
    conversation.record_wait("dedupe")               # 3 (never shown)
    conversation.record_reply("hello!")              # 4
    conversation.record_draft("Draft r-1\nTo: a@x.com")  # 5
    shown = [(m.seq, m.role, m.content) for m in conversation.to_chat_messages()]
    assert shown == [(1, "user", "hi"), (4, "assistant", "hello!"), (5, "draft", "Draft r-1\nTo: a@x.com")]
    assert [m.seq for m in conversation.to_chat_messages(after=4)] == [5]
    assert conversation.entry_count() == 5


def test_numbering_continues_across_restarts_and_restarts_after_a_clear(conversation, tmp_path):
    conversation.record_user_message("one")
    conversation.record_reply("two")
    reopened = ConversationLog(tmp_path / "conversation.log")
    reopened._working_memory_log = conversation._working_memory_log
    reopened.record_user_message("three")
    assert [m.seq for m in reopened.to_chat_messages()] == [1, 2, 3]
    reopened.clear()
    assert reopened.entry_count() == 0
    reopened.record_user_message("fresh")
    assert [m.seq for m in reopened.to_chat_messages()] == [1]


async def test_chat_entries_are_published_as_they_are_written(conversation):
    queue = get_conversation_events().subscribe()
    try:
        conversation.record_user_message("hi")
        conversation.record_agent_message("internal")
        conversation.record_reply("hello!")
        conversation.clear()
        await settle()
        events = drain(queue)
    finally:
        get_conversation_events().unsubscribe(queue)
    assert [(e["type"], e.get("seq"), e.get("role"), e.get("content")) for e in events] == [
        ("message", 1, "user", "hi"), ("message", 3, "assistant", "hello!"), ("cleared", None, None, None)]
    assert all(e["timestamp"] for e in events if e["type"] == "message")


# --- The SSE stream ---------------------------------------------------------------------------

class Connection:
    """Stands in for the HTTP request: the stream stops once the client 'disconnects'."""

    def __init__(self):
        self.closed = False

    async def is_disconnected(self):
        return self.closed


def parse(chunk):
    fields = dict(line.split(": ", 1) for line in chunk.strip().splitlines() if not line.startswith(":") and ": " in line)
    if "data" in fields:
        fields["data"] = json.loads(fields["data"])
    return fields


async def take(stream, count):
    return [parse(await stream.__anext__()) for _ in range(count)]


@pytest.fixture
def log(clean_state):
    return get_conversation_log()


async def test_stream_replays_what_the_client_missed_then_the_working_state(log):
    log.record_user_message("hi")
    log.record_reply("hello!")
    log.record_user_message("again")
    stream = conversation_event_stream(1, Connection().is_disconnected)
    retry, *events = await take(stream, 4)
    assert retry == {"retry": "3000"}
    assert [(e["event"], e.get("id"), e["data"]) for e in events] == [
        ("message", "2", {"seq": 2, "role": "assistant", "content": "hello!", "timestamp": events[0]["data"]["timestamp"]}),
        ("message", "3", {"seq": 3, "role": "user", "content": "again", "timestamp": events[1]["data"]["timestamp"]}),
        ("status", None, {"working": False}),
    ]
    await stream.aclose()
    assert get_conversation_events().subscriber_count == 0


async def test_stream_pushes_new_messages_working_state_and_clears_live(log):
    stream = conversation_event_stream(0, Connection().is_disconnected)
    await take(stream, 2)  # retry + status
    next_event = asyncio.ensure_future(stream.__anext__())
    await settle()
    log.record_user_message("hi")
    assert parse(await next_event)["data"]["content"] == "hi"
    get_conversation_events().work_started()
    assert parse(await stream.__anext__()) == {"event": "status", "data": {"working": True}}
    get_conversation_events().work_finished()
    assert parse(await stream.__anext__())["data"] == {"working": False}
    log.clear()
    assert parse(await stream.__anext__()) == {"id": "0", "event": "cleared", "data": {}}
    log.record_user_message("fresh")
    fresh = parse(await stream.__anext__())
    assert (fresh["id"], fresh["data"]["content"]) == ("1", "fresh")  # numbering restarted, and still delivered
    await stream.aclose()


async def test_a_message_already_in_the_backlog_is_not_sent_twice(log):
    stream = conversation_event_stream(0, Connection().is_disconnected)
    log.record_user_message("hi")  # written after subscribing, so it's both in the backlog and queued live
    first = await take(stream, 3)  # retry, backlog message, status
    assert [e.get("event") for e in first] == [None, "message", "status"]
    log.record_reply("hello!")
    nxt = parse(await stream.__anext__())
    assert nxt["data"]["content"] == "hello!"  # the queued copy of "hi" was skipped
    await stream.aclose()


async def test_a_client_that_missed_a_clear_starts_over(log):
    log.record_user_message("after the clear")
    stream = conversation_event_stream(40, Connection().is_disconnected)  # it last saw seq 40 of the old history
    events = await take(stream, 4)
    assert [e.get("event") for e in events] == [None, "cleared", "message", "status"]
    assert events[2]["data"]["content"] == "after the clear"
    await stream.aclose()


async def test_idle_streams_send_heartbeats_and_stop_when_the_client_leaves(log, monkeypatch):
    monkeypatch.setattr(stream_module, "get_settings", lambda: Settings(event_stream_heartbeat_seconds=0.01))
    connection = Connection()
    stream = conversation_event_stream(0, connection.is_disconnected)
    await take(stream, 2)
    assert await stream.__anext__() == ": ping\n\n"
    connection.closed = True
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    assert get_conversation_events().subscriber_count == 0


# --- Working state from real work ------------------------------------------------------------

async def test_an_interaction_turn_is_working_from_start_to_finish(fake_llm, clean_state):
    from server.agents.interaction_agent.runtime import InteractionAgentRuntime

    queue = get_conversation_events().subscribe()
    try:
        fake_llm.for_interaction(calls(("send_message_to_user", {"message": "on it"})), reply(""))
        await InteractionAgentRuntime().execute("hi")
        await settle()
        events = [(e["type"], e.get("working"), e.get("content")) for e in drain(queue)]
    finally:
        get_conversation_events().unsubscribe(queue)
    assert events == [("status", True, None), ("message", None, "hi"), ("message", None, "on it"), ("status", False, None)]


async def test_a_failed_turn_still_ends_working(fake_llm, clean_state):
    from server.agents.interaction_agent.runtime import InteractionAgentRuntime
    from server.openrouter_client import OpenRouterError

    fake_llm.for_interaction(OpenRouterError("503"))
    await InteractionAgentRuntime().execute("hi")
    assert get_conversation_events().working is False


@pytest.mark.parametrize("report", [True, False])
async def test_only_work_the_user_asked_for_shows_as_working(fake_llm, fake_gmail, clean_state, batch_manager, report):
    from server.services import get_agent_registry

    agent = get_agent_registry().create("Lunch")
    fake_llm.for_execution(reply("done"))
    fake_llm.for_interaction(reply(""))
    queue = get_conversation_events().subscribe()
    try:
        await batch_manager.execute_agent(agent.id, "go", report=report)
        await settle()
        statuses = [e["working"] for e in drain(queue) if e["type"] == "status"]
    finally:
        get_conversation_events().unsubscribe(queue)
    if report:  # the run itself; the interaction turn that relays its report may follow
        assert statuses[:2] == [True, False]
    else:  # a background trigger run never shows the assistant as working
        assert statuses == []


# --- The HTTP route -----------------------------------------------------------------------------

class FakeRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize("after, header, start", [(None, None, 0), (None, "2", 2), (1, "2", 1), (None, "junk", 0)])
async def test_route_resumes_from_after_or_the_last_event_id(log, after, header, start):
    from server.routes.chat import chat_events

    for text in ("a", "b", "c"):
        log.record_user_message(text)
    response = await chat_events(FakeRequest(), after=after, last_event_id=header)
    assert response.media_type == "text/event-stream" and response.headers["cache-control"] == "no-cache"
    body = response.body_iterator
    await body.__anext__()  # retry
    first = parse(await body.__anext__())
    assert first["data"]["seq"] == start + 1
    await body.aclose()
