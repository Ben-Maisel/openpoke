"""Tests for the per-agent execution log store (file-backed, no LLM)."""

import pytest

from server.services.execution.log_store import ExecutionAgentLogStore, _slugify


@pytest.fixture
def store(tmp_path):
    return ExecutionAgentLogStore(tmp_path / "execution_agents")


def test_records_full_agent_lifecycle_in_order(store):
    store.record_request("Lunch with Alice", "Draft an invite for Friday")
    store.record_action("Lunch with Alice", "Calling gmail_create_draft")
    store.record_tool_response("Lunch with Alice", "gmail_create_draft", '{"id": "r-1"}')
    store.record_agent_response("Lunch with Alice", "Draft r-1 created")

    entries = list(store.iter_entries("Lunch with Alice"))
    assert [(tag, payload) for tag, _, payload in entries] == [
        ("agent_request", "Draft an invite for Friday"),
        ("agent_action", "Calling gmail_create_draft"),
        ("tool_response", 'gmail_create_draft: {"id": "r-1"}'),
        ("agent_response", "Draft r-1 created"),
    ]
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


@pytest.mark.xfail(strict=True, reason="P24: literal backslash-n is decoded as a newline")
def test_literal_backslash_n_round_trips(store):
    payload = r"attachment saved to C:\new\notes.txt"
    store.record_tool_response("a", "gmail_fetch", payload)
    (_, _, stored), = store.iter_entries("a")
    assert stored == f"gmail_fetch: {payload}"


def test_transcript_is_tagged_and_escaped_for_the_prompt(store):
    store.record_request("a", "compare <x> & <y>")
    transcript = store.load_transcript("a")
    assert transcript.startswith('<agent_request timestamp="')
    assert "compare &lt;x&gt; &amp; &lt;y&gt;" in transcript


def test_load_recent_returns_last_n_entries(store):
    for i in range(15):
        store.record_action("a", f"step {i}")
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


@pytest.mark.xfail(strict=True, reason="P4: names differing only in case/punctuation share one log file")
def test_distinct_agent_names_never_share_memory(store):
    store.record_request("Email to Alice", "draft the invite")
    store.record_request("email to alice!", "unrelated task")
    assert [p for _, _, p in store.iter_entries("Email to Alice")] == ["draft the invite"]
