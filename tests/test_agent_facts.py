"""Tests for card facts derived from tool calls (P1c)."""

import pytest

from fakes import calls, reply
from server.agents.execution_agent.facts import extract_facts
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services import get_agent_registry, get_draft_store


def added(tool, args, result=None):
    return {kind: sorted(values) for kind, values in extract_facts(tool, args, result).add.items()}


def removed(tool, args, result=None):
    return {kind: sorted(values) for kind, values in extract_facts(tool, args, result).remove.items()}


# --- Extraction rules ------------------------------------------------------------

def test_draft_records_recipients_thread_and_open_draft():
    args = {"recipient_email": "Alice.Wong@Acme.com", "subject": "s", "body": "b",
            "cc": ["David Kim <david.kim@acme.com>"], "thread_id": "t-9"}
    assert added("gmail_create_draft", args, {"successful": True, "data": {"id": "r-1"}}) == {
        "email": ["alice.wong@acme.com", "david.kim@acme.com"],
        "person": ["David Kim"],
        "thread": ["t-9"],
        "draft": ["r-1"],
    }


@pytest.mark.parametrize("result", [{"data": {"draft_id": "r-2"}}, {"data": {"draftId": "r-2"}}, {"id": "r-2"}])
def test_draft_id_is_found_in_any_common_response_shape(result):
    assert added("gmail_create_draft", {"recipient_email": "a@x.com"}, result)["draft"] == ["r-2"]


@pytest.mark.parametrize("tool", ["gmail_execute_draft", "gmail_delete_draft"])
def test_sent_or_deleted_drafts_are_no_longer_open(tool):
    assert removed(tool, {"draft_id": "r-1"}, {"successful": True}) == {"draft": ["r-1"]}


def test_reply_drafts_and_prepared_forwards_record_who_and_stay_open():
    assert added("gmail_create_draft", {"thread_id": "t-1", "recipient_email": "ben@figma.com", "subject": "Re: x",
                                        "body": "x"},
                 {"successful": True, "data": {"id": "r-3"}}) == {"email": ["ben@figma.com"], "thread": ["t-1"],
                                                                  "draft": ["r-3"]}
    assert added("gmail_forward_email", {"message_id": "m-1", "recipient_email": "Jake <jake@law.com>"},
                 {"successful": True, "data": {"id": "fwd-1"}}) == {"email": ["jake@law.com"], "person": ["Jake"],
                                                                    "draft": ["fwd-1"]}


def test_search_results_record_senders_and_threads():
    results = [
        {"id": "m-1", "thread_id": "t-1", "sender": "Alice Wong <alice.wong@acme.com>", "recipient": "me@x.com"},
        {"id": "m-2", "thread_id": "t-2", "sender": "billing@stripe.com", "recipient": "me@x.com"},
    ]
    assert added("task_email_search", {"search_query": "q"}, results) == {
        "email": ["alice.wong@acme.com", "billing@stripe.com", "me@x.com"],
        "person": ["Alice Wong"],
        "thread": ["t-1", "t-2"],
    }


def test_a_broad_search_cannot_flood_the_card():
    results = [{"thread_id": f"t-{i:03}", "sender": f"user{i}@x.com"} for i in range(100)]
    facts = extract_facts("task_email_search", {}, results).add
    assert len(facts["thread"]) == 20 and len(facts["email"]) == 20


def test_live_triggers_are_tracked_and_dropped_when_stopped():
    assert added("createTrigger", {"payload": "p"}, {"trigger_id": 7, "status": "active"}) == {"trigger": ["7"]}
    assert added("createTrigger", {"payload": "p"}, {"trigger_id": 8, "status": "paused"}) == {}
    assert removed("updateTrigger", {"trigger_id": 7}, {"trigger_id": 7, "status": "paused"}) == {"trigger": ["7"]}
    assert added("updateTrigger", {"trigger_id": 7}, {"trigger_id": 7, "status": "active"}) == {"trigger": ["7"]}


@pytest.mark.parametrize(
    "tool, result",
    [
        ("gmail_create_draft", {"error": "Gmail not connected"}),
        ("gmail_create_draft", {"successful": False, "error": "quota"}),
        ("createTrigger", {"error": "bad rule"}),
    ],
)
def test_failed_calls_record_nothing(tool, result):
    changes = extract_facts(tool, {"recipient_email": "a@x.com", "payload": "p"}, result)
    assert (changes.add, changes.remove) == ({}, {})


def test_lookups_and_unknown_tools_record_nothing():
    assert added("gmail_search_people", {"query": "alice"}, {"successful": True}) == {}
    assert added("teleport", {"to": "mars"}, {"ok": True}) == {}


# --- Recorded on the card during a real run --------------------------------------

@pytest.fixture
def agent(clean_state):
    return get_agent_registry().create("Lunch with Alice")


async def test_a_run_updates_the_agents_card(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(
        calls(("gmail_create_draft", {"recipient_email": "alice.wong@acme.com", "subject": "Lunch?", "body": "Fri?"})),
        calls(("createTrigger", {"payload": "Check if Alice replied", "start_time": "2030-01-01T09:00:00Z"})),
        reply("Drafted r-1 and set a follow-up reminder"),
    )
    await ExecutionAgentRuntime(agent.id).execute("Invite Alice Wong to lunch and remind me to follow up")
    facts = get_agent_registry().facts(agent.id)
    assert facts["email"] == ["alice.wong@acme.com"]
    assert facts["draft"] == ["r-1"]
    assert len(facts["trigger"]) == 1


async def test_sending_the_draft_closes_it_on_the_card(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(
        calls(("gmail_create_draft", {"recipient_email": "alice@x.com", "subject": "s", "body": "b"})), reply("drafted"),
        calls(("gmail_execute_draft", {"draft_id": "r-1"})), reply("sent"),
    )
    await ExecutionAgentRuntime(agent.id).execute("draft it")
    assert get_agent_registry().facts(agent.id)["draft"] == ["r-1"]
    get_draft_store().mark_shown("r-1")
    get_draft_store().approve("r-1", "send it")
    await ExecutionAgentRuntime(agent.id).execute("send it")
    facts = get_agent_registry().facts(agent.id)
    assert facts["draft"] == [] and facts["email"] == ["alice@x.com"]


async def test_a_refused_send_keeps_the_draft_open_on_the_card(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(
        calls(("gmail_create_draft", {"recipient_email": "alice@x.com", "subject": "s", "body": "b"})),
        calls(("gmail_execute_draft", {"draft_id": "r-1"})), reply("not approved yet"),
    )
    await ExecutionAgentRuntime(agent.id).execute("draft and send it")
    assert get_agent_registry().facts(agent.id)["draft"] == ["r-1"]


async def test_failed_tool_calls_leave_the_card_unchanged(fake_llm, fake_gmail, agent):
    fake_gmail.connected = False
    fake_llm.for_execution(calls(("gmail_create_draft", {"recipient_email": "a@x.com", "subject": "s", "body": "b"})),
                           reply("Gmail isn't connected"))
    await ExecutionAgentRuntime(agent.id).execute("draft it")
    assert all(values == [] for values in get_agent_registry().facts(agent.id).values())
