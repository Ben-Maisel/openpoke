"""Tests for the per-agent execution log store (file-backed, no LLM)."""

import json

import pytest

from server.services.execution.log_store import ExecutionAgentLogStore, _slugify


@pytest.fixture
def store(tmp_path):
    return ExecutionAgentLogStore(tmp_path / "execution_agents")


def test_records_full_agent_lifecycle_in_order(store):
    store.record_request("Lunch with Alice", "Draft an invite for Friday")
    store.record_tool_call("Lunch with Alice", "gmail_create_draft", {"subject": "Lunch"}, True, {"id": "r-1"})
    store.record_agent_response("Lunch with Alice", "Draft r-1 created")

    entries = list(store.iter_entries("Lunch with Alice"))
    assert [tag for tag, _, _ in entries] == ["agent_request", "tool_call", "agent_response"]
    assert json.loads(entries[1][2]) == {
        "ref": "r1", "tool": "gmail_create_draft", "ok": True, "arguments": {"subject": "Lunch"}, "result": {"id": "r-1"},
    }
    assert all(timestamp for _, timestamp, _ in entries)


def test_unknown_agent_has_empty_history(store):
    assert list(store.iter_entries("never created")) == []
    assert store.load_transcript("never created") == ""
    assert store.load_recent("never created") == []


def test_agents_have_separate_histories(store):
    store.record_request("Lunch with Alice", "a")
    store.record_request("Vercel Job Offer", "b")
    assert [p for _, _, p in store.iter_entries("Lunch with Alice")] == ["a"]
    assert [p for _, _, p in store.iter_entries("Vercel Job Offer")] == ["b"]


def test_multiline_and_markup_payloads_round_trip(store):
    payload = "To: alice@example.com\nSubject: Lunch <Fri> & drinks\n\nBody"
    store.record_agent_response("a", payload)
    (_, _, stored), = store.iter_entries("a")
    assert stored == payload


def test_literal_backslash_n_round_trips(store):
    payload = r"attachment saved to C:\new\notes.txt"
    store.record_agent_response("a", payload)
    (_, _, stored), = store.iter_entries("a")
    assert stored == payload


def test_tool_calls_are_stored_in_full_never_truncated(store):
    body = "x" * 20_000 + "\nTHE END <b> & \\n"
    result = [{"id": f"m-{i}", "clean_text": body} for i in range(10)]
    store.record_tool_call("a", "task_email_search", {"search_query": "q"}, True, result)
    (_, _, stored), = store.iter_entries("a")
    assert json.loads(stored)["result"] == result


def test_tool_call_refs_count_up_per_agent(store):
    refs = [store.record_tool_call("a", "t", {}, True, None) for _ in range(3)]
    store.record_request("a", "between calls")
    refs.append(store.record_tool_call("a", "t", {}, False, {"error": "boom"}))
    assert refs == ["r1", "r2", "r3", "r4"]
    assert store.record_tool_call("b", "t", {}, True, None) == "r1"


def test_unserializable_results_are_stored_as_text(store):
    from datetime import datetime

    store.record_tool_call("a", "t", {}, True, {"when": datetime(2026, 10, 27, 9, 30)})
    (_, _, stored), = store.iter_entries("a")
    assert json.loads(stored)["result"] == {"when": "2026-10-27 09:30:00"}


def test_transcript_is_tagged_and_escaped_for_the_prompt(store):
    store.record_request("a", "compare <x> & <y>")
    transcript = store.load_transcript("a")
    assert transcript.startswith('<agent_request timestamp="')
    assert "compare &lt;x&gt; &amp; &lt;y&gt;" in transcript


def test_load_recent_returns_last_n_entries(store):
    for i in range(15):
        store.record_agent_response("a", f"step {i}")
    recent = store.load_recent("a", limit=3)
    assert [p for _, _, p in recent] == ["step 12", "step 13", "step 14"]


def test_clear_all_removes_every_agent_log(store):
    store.record_request("a", "x")
    store.record_request("b", "y")
    store.clear_all()
    assert store.list_agents() == []


@pytest.mark.parametrize(
    "name, slug",
    [
        ("Lunch with Alice", "lunch-with-alice"),
        ("  Email to Bob!!  ", "email-to-bob"),
        ("Q3 / Q4 planning", "q3-q4-planning"),
        ("Café meeting", "café-meeting"),
        ("!!!", "agent"),
        ("", "agent"),
    ],
)
def test_slugify(name, slug):
    assert _slugify(name) == slug


def test_agents_whose_names_collide_never_share_memory(store, tmp_path):
    # Before P1, memory was keyed by the slugified name, so these two shared one file (P4).
    from server.services.execution.registry import AgentRegistry

    registry = AgentRegistry(tmp_path / "registry.db")
    first = registry.create("Email to Alice")
    second = registry.create("email to alice!")
    store.record_request(first.id, "draft the invite")
    store.record_request(second.id, "unrelated task")
    assert [p for _, _, p in store.iter_entries(first.id)] == ["draft the invite"]
    assert [p for _, _, p in store.iter_entries(second.id)] == ["unrelated task"]
