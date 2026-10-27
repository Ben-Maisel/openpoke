"""Tests for the recall tool: an execution agent looking back through its own full journal (no LLM)."""

import json

import pytest

from fakes import calls, is_execution_agent, reply
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.agents.execution_agent.tools import get_tool_registry, get_tool_schemas
from server.agents.execution_agent.tools.recall import MATCH_CHARS, MAX_MATCHES, RECORD_CHARS
from server.services import get_agent_registry, get_execution_agent_logs

pytestmark = pytest.mark.usefixtures("clean_state")


def recall(agent_id="a1", **kwargs):
    return get_tool_registry(agent_id)["recall"](**kwargs)


def logs():
    return get_execution_agent_logs()


def search_result(*emails):
    return [{"id": i, "thread_id": f"t-{i}", "sender": s, "recipient": "me@x.com", "subject": subj,
             "timestamp": "2026-10-01 09:00:00", "clean_text": text} for i, s, subj, text in emails]


def test_recall_is_offered_to_every_worker():
    assert "recall" in [schema["function"]["name"] for schema in get_tool_schemas()]


# --- By reference -------------------------------------------------------------------

def test_ref_returns_the_complete_record_even_what_the_digest_left_out():
    body = "Full itinerary: " + "flight details. " * 200 + "Hotel confirmation HX-4471."
    logs().record_tool_call("a1", "task_email_search", {"search_query": "trip"}, True,
                            search_result(("m-1", "Travel <t@x.com>", "Your trip", body)))
    result = recall(ref="r1")
    assert result["ref"] == "r1" and result["timestamp"]
    assert result["record"]["result"][0]["clean_text"] == body


def test_huge_records_are_cut_with_a_note_but_stay_whole_on_disk():
    big = [{"id": f"m-{i}", "clean_text": "y" * 5_000} for i in range(10)]
    logs().record_tool_call("a1", "task_email_search", {"search_query": "q"}, True, big)
    result = recall(ref="r1")
    assert len(result["record"]) == RECORD_CHARS
    assert result["note"].startswith(f"Cut at {RECORD_CHARS} of ")
    (_, _, stored), = logs().iter_entries("a1")
    assert json.loads(stored)["result"] == big


def test_unknown_ref_explains_itself():
    logs().record_tool_call("a1", "t", {}, True, None)
    assert "No tool call 'r9'" in recall(ref="r9")["error"]
    assert "No tool call" in recall(agent_id="a2", ref="r1")["error"]  # refs are per agent


@pytest.mark.parametrize("kwargs", [{}, {"ref": "r1", "query": "alice"}, {"ref": "  ", "query": ""}])
def test_exactly_one_of_ref_or_query_is_required(kwargs):
    assert recall(**kwargs) == {"error": "Pass exactly one of `ref` (e.g. 'r12') or `query`."}


# --- By query -----------------------------------------------------------------------

def test_query_finds_old_runs_and_shows_them_as_digests():
    logs().record_request("a1", "Find the Denver hotel booking")
    logs().record_tool_call("a1", "task_email_search", {"search_query": "hotel"}, True,
                            search_result(("m-7", "Marriott <res@marriott.com>", "Booking", "Confirmation HX-4471 for Denver")))
    logs().record_agent_response("a1", "Found it.")
    for i in range(50):
        logs().record_request("a1", f"Unrelated check {i}")
        logs().record_agent_response("a1", f"Nothing new {i}")

    result = recall(query="HX-4471 confirmation")
    assert result["searched_entries"] == 103
    top = result["matches"][0]
    assert top["kind"] == "tool_call"
    assert top["text"].startswith('task_email_search [r1]: searched "hotel" → 1 email(s)')
    assert "m-7 (thread t-m-7)" in top["text"]


def test_rare_words_outrank_common_ones():
    for i in range(5):
        logs().record_agent_response("a1", f"Alice update {i}")
    logs().record_agent_response("a1", "Alice said the venue is Nopa")
    logs().record_agent_response("a1", "Bob mentioned Nopa once")
    texts = [m["text"] for m in recall(query="Alice Nopa")["matches"]]
    assert texts[0] == "Alice said the venue is Nopa"
    assert texts[1] == "Bob mentioned Nopa once"  # the rare word alone beats the common one alone


def test_newer_entries_win_ties_and_results_are_capped():
    for i in range(MAX_MATCHES + 3):
        logs().record_agent_response("a1", f"Lunch plan version {i}")
    texts = [m["text"] for m in recall(query="lunch")["matches"]]
    assert texts == [f"Lunch plan version {i}" for i in range(MAX_MATCHES + 2, 2, -1)]


def test_long_matches_are_shortened():
    logs().record_agent_response("a1", "Itinerary " + "z" * 5_000)
    (match,) = recall(query="itinerary")["matches"]
    assert len(match["text"]) == MATCH_CHARS + 1 and match["text"].endswith("…")


def test_earlier_recalls_are_not_searched():
    logs().record_agent_response("a1", "Gift idea: blue scarf")
    logs().record_tool_call("a1", "recall", {"query": "scarf"}, True, {"matches": [{"text": "Gift idea: blue scarf"}]})
    result = recall(query="scarf")
    assert result["searched_entries"] == 1
    assert [m["text"] for m in result["matches"]] == ["Gift idea: blue scarf"]


def test_no_matches_and_empty_queries():
    logs().record_agent_response("a1", "Something")
    assert recall(query="zebra")["matches"] == []
    assert "no searchable words" in recall(query="the email to my")["error"]  # only generic words


# --- Through the real runtime --------------------------------------------------------

async def test_worker_can_recall_a_full_record_mid_run(fake_llm, fake_gmail):
    agent = get_agent_registry().create("Denver trip")
    body = "Check-in at 3pm. " + "Policy text. " * 100 + "Door code 8812."
    logs().record_tool_call(agent.id, "task_email_search", {"search_query": "hotel"}, True,
                            search_result(("m-1", "Hotel <h@x.com>", "Your stay", body)))
    fake_llm.for_execution(calls(("recall", {"ref": "r1"})), reply("The door code is 8812."))

    result = await ExecutionAgentRuntime(agent.id).execute("What's the hotel door code?")

    assert result.response == "The door code is 8812."
    fed_back = json.loads(fake_llm.calls_to(is_execution_agent)[1].messages[-1]["content"])
    assert fed_back["result"]["record"]["result"][0]["clean_text"] == body
    last = [p for t, _, p in logs().iter_entries(agent.id) if t == "tool_call"][-1]
    assert json.loads(last)["ref"] == "r2"
