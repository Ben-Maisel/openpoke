"""Tests for the conversation summarization rules, with the LLM call faked."""

import pytest

from server.config import Settings
from server.openrouter_client import OpenRouterError
from server.services.conversation.log import ConversationLog
from server.services.conversation.summarization import summarizer as summarizer_module
from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog

THRESHOLD = 4
TAIL = 2


@pytest.fixture
def memory(tmp_path):
    return WorkingMemoryLog(tmp_path / "working_memory.log")


@pytest.fixture
def conversation(tmp_path, memory):
    log = ConversationLog(tmp_path / "conversation.log")
    log._working_memory_log = memory
    return log


@pytest.fixture
def llm(monkeypatch, conversation, memory):
    """Fake summarizer LLM. Tests can set `llm['reply']` or `llm['during_call']`."""
    state = {"calls": [], "reply": "SUMMARY v1", "during_call": None}

    async def fake_call(prompt, model, api_key):
        state["calls"].append(prompt)
        if state["during_call"]:
            state["during_call"]()
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"]

    settings = Settings(conversation_summary_threshold=THRESHOLD, conversation_summary_tail_size=TAIL)
    monkeypatch.setattr(summarizer_module, "get_settings", lambda: settings)
    monkeypatch.setattr(summarizer_module, "_resolve_conversation_log", lambda: conversation)
    monkeypatch.setattr(summarizer_module, "get_working_memory_log", lambda: memory)
    monkeypatch.setattr(summarizer_module, "_call_openrouter", fake_call)
    return state


def _add_messages(conversation, count, start=0):
    for i in range(start, start + count):
        conversation.record_user_message(f"message {i}")


async def test_does_nothing_below_threshold_plus_tail(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL - 1)
    assert await summarizer_module.summarize_conversation() is False
    assert llm["calls"] == []
    assert memory.load_summary_state().last_index == -1


async def test_summarizes_oldest_batch_and_keeps_the_rest(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL)
    assert await summarizer_module.summarize_conversation() is True

    prompt_text = llm["calls"][0].messages[0]["content"]
    assert "message 0" in prompt_text and "message 3" in prompt_text
    assert "message 4" not in prompt_text

    state = memory.load_summary_state()
    assert state.summary_text == "SUMMARY v1"
    assert state.last_index == THRESHOLD - 1
    assert [e.payload for e in state.unsummarized_entries] == ["message 4", "message 5"]


async def test_rendered_transcript_after_summary_is_summary_plus_recent(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL)
    await summarizer_module.summarize_conversation()
    lines = memory.render_transcript().splitlines()
    assert lines[0] == "<conversation_summary>SUMMARY v1</conversation_summary>"
    assert len(lines) == 1 + TAIL


async def test_second_pass_only_counts_new_entries_and_feeds_previous_summary(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL)
    await summarizer_module.summarize_conversation()

    _add_messages(conversation, THRESHOLD - 1, start=6)  # 2 left over + 3 new = 5 < 6
    assert await summarizer_module.summarize_conversation() is False

    conversation.record_user_message("message 9")  # now 6 unsummarized
    llm["reply"] = "SUMMARY v2"
    assert await summarizer_module.summarize_conversation() is True

    second_prompt = llm["calls"][1].messages[0]["content"]
    assert "SUMMARY v1" in second_prompt
    assert "message 3" not in second_prompt
    state = memory.load_summary_state()
    assert state.summary_text == "SUMMARY v2"
    assert [e.payload for e in state.unsummarized_entries] == ["message 8", "message 9"]


async def test_messages_arriving_during_the_llm_call_are_kept(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL)
    llm["during_call"] = lambda: conversation.record_user_message("arrived mid-summary")
    await summarizer_module.summarize_conversation()
    payloads = [e.payload for e in memory.load_summary_state().unsummarized_entries]
    assert payloads[-1] == "arrived mid-summary"


async def test_llm_failure_leaves_memory_unchanged(conversation, memory, llm):
    _add_messages(conversation, THRESHOLD + TAIL)
    llm["reply"] = OpenRouterError("rate limited")
    with pytest.raises(OpenRouterError):
        await summarizer_module.summarize_conversation()
    state = memory.load_summary_state()
    assert (state.summary_text, state.last_index) == ("", -1)
    assert len(state.unsummarized_entries) == THRESHOLD + TAIL


async def test_disabled_when_threshold_is_zero(conversation, monkeypatch, llm):
    monkeypatch.setattr(summarizer_module, "get_settings", lambda: Settings(conversation_summary_threshold=0))
    _add_messages(conversation, 50)
    assert await summarizer_module.summarize_conversation() is False
    assert llm["calls"] == []
