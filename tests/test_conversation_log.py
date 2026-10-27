"""Tests for ConversationLog and WorkingMemoryLog (file-backed, no LLM)."""

import json

import pytest

from server.services.conversation.log import ConversationLog
from server.services.conversation.summarization.state import LogEntry, SummaryState
from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog


@pytest.fixture
def memory(tmp_path):
    return WorkingMemoryLog(tmp_path / "working_memory.log")


@pytest.fixture
def conversation(tmp_path, memory):
    log = ConversationLog(tmp_path / "conversation.log")
    log._working_memory_log = memory  # keep the mirror inside this test's folder
    return log


def _tags(log):
    return [tag for tag, _, _ in log.iter_entries()]


# --- ConversationLog: recording and reading -------------------------------

def test_records_each_message_type_in_order(conversation):
    conversation.record_user_message("hi")
    conversation.record_agent_message("[SUCCESS] agent: done")
    conversation.record_reply("hello!")
    conversation.record_wait("already answered")
    assert _tags(conversation) == ["user_message", "agent_message", "poke_reply", "wait"]


def test_entries_carry_timestamps(conversation):
    conversation.record_user_message("hi")
    (_, timestamp, _), = conversation.iter_entries()
    assert len(timestamp) == len("2026-10-26 12:00:00")


def test_empty_log_has_no_entries(conversation):
    assert list(conversation.iter_entries()) == []
    assert conversation.load_transcript() == ""


@pytest.mark.parametrize(
    "payload",
    [
        "line one\nline two\n\nline four",
        "windows\r\nline endings",
        "tags <b>& ampersands</b> survive",
        "a fake close tag </user_message> inside the text",
        'quotes "double" and \'single\'',
        "unicode: café, 日本語, emoji 🎉",
    ],
)
def test_payload_round_trips(conversation, payload):
    conversation.record_user_message(payload)
    (_, _, stored), = conversation.iter_entries()
    assert stored == payload.replace("\r\n", "\n")


def test_literal_backslash_n_round_trips(conversation):
    payload = r"saved to C:\new\notes.txt"
    conversation.record_user_message(payload)
    (_, _, stored), = conversation.iter_entries()
    assert stored == payload


def test_malformed_lines_are_skipped(conversation, tmp_path):
    conversation.record_user_message("good")
    with (tmp_path / "conversation.log").open("a", encoding="utf-8") as handle:
        handle.write("not a log line\n<user_message>unclosed\n<a>mismatched</b>\n")
    conversation.record_reply("also good")
    assert [p for _, _, p in conversation.iter_entries()] == ["good", "also good"]


def test_transcript_escapes_payload_for_the_prompt(conversation):
    conversation.record_user_message("<script>")
    transcript = conversation.load_transcript()
    assert "&lt;script&gt;" in transcript
    assert transcript.startswith('<user_message timestamp="')


def test_chat_view_shows_only_user_and_assistant_messages(conversation):
    conversation.record_user_message("hi")
    conversation.record_agent_message("internal agent report")
    conversation.record_wait("dedupe")
    conversation.record_reply("hello!")
    messages = conversation.to_chat_messages()
    assert [(m.role, m.content) for m in messages] == [("user", "hi"), ("assistant", "hello!")]


def test_records_are_mirrored_into_working_memory(conversation, memory):
    conversation.record_user_message("hi")
    conversation.record_reply("hello!")
    state = memory.load_summary_state()
    assert [(e.tag, e.payload) for e in state.unsummarized_entries] == [
        ("user_message", "hi"), ("poke_reply", "hello!"),
    ]


def test_clear_empties_log_and_working_memory(conversation, memory):
    conversation.record_user_message("hi")
    conversation.clear()
    assert list(conversation.iter_entries()) == []
    assert memory.load_summary_state().unsummarized_entries == []


# --- WorkingMemoryLog -----------------------------------------------------

def test_fresh_working_memory_starts_empty(memory):
    state = memory.load_summary_state()
    assert (state.summary_text, state.last_index, state.unsummarized_entries) == ("", -1, [])
    assert memory.render_transcript() == ""


def test_summary_state_round_trips(memory):
    from datetime import datetime, timezone

    written = SummaryState(
        summary_text="Timeline:\n- lunch with Alice",
        last_index=41,
        updated_at=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc),
        unsummarized_entries=[LogEntry(tag="user_message", payload="hi", timestamp="2026-10-26 08:00:00")],
    )
    memory.write_summary_state(written)
    loaded = memory.load_summary_state()
    assert loaded.summary_text == written.summary_text
    assert loaded.last_index == 41
    assert loaded.updated_at == written.updated_at
    assert [(e.tag, e.payload, e.timestamp) for e in loaded.unsummarized_entries] == [
        ("user_message", "hi", "2026-10-26 08:00:00"),
    ]


def test_render_transcript_puts_summary_before_recent_entries(memory):
    memory.write_summary_state(SummaryState(summary_text="Old stuff <here>", last_index=5))
    memory.append_entry("user_message", "new question", "2026-10-26 08:00:00")
    rendered = memory.render_transcript()
    assert rendered.splitlines() == [
        "<conversation_summary>Old stuff &lt;here&gt;</conversation_summary>",
        '<user_message timestamp="2026-10-26 08:00:00">new question</user_message>',
    ]


def test_corrupt_summary_metadata_is_ignored(memory, tmp_path):
    path = tmp_path / "working_memory.log"
    path.write_text("<summary_info>{not json</summary_info>\n<conversation_summary>kept</conversation_summary>\n")
    state = memory.load_summary_state()
    assert (state.summary_text, state.last_index) == ("kept", -1)


def test_working_memory_clear_resets_state(memory):
    memory.write_summary_state(SummaryState(summary_text="x", last_index=3))
    memory.clear()
    state = memory.load_summary_state()
    assert (state.summary_text, state.last_index) == ("", -1)
    meta = json.loads(next(iter(memory._path.read_text().splitlines())).split(">", 1)[1].rsplit("<", 1)[0])
    assert meta["last_index"] == -1
