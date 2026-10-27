"""Component tests for the execution agent loop, with a scripted LLM and fake Gmail."""

import json

import pytest

from fakes import calls, is_execution_agent, reply
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.openrouter_client import OpenRouterError
from server.services import get_agent_registry, get_execution_agent_logs

pytestmark = pytest.mark.usefixtures("clean_state")

DRAFT_ARGS = {"recipient_email": "alice@example.com", "subject": "Lunch Friday?", "body": "Are you free at noon?"}


@pytest.fixture
def agent(clean_state):
    return get_agent_registry().create("Lunch with Alice", summary="Invite Alice to lunch")


def history(agent_id):
    return [(tag, payload) for tag, _, payload in get_execution_agent_logs().iter_entries(agent_id)]


def tool_calls(agent_id):
    return [json.loads(payload) for tag, payload in history(agent_id) if tag == "tool_call"]


def tool_results(call):
    return [json.loads(m["content"]) for m in call.messages if m["role"] == "tool"]


# --- The basic loop -------------------------------------------------------

async def test_answer_without_tools_is_returned_and_logged(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(reply("Nothing needed; Alice already confirmed."))
    result = await ExecutionAgentRuntime(agent.id).execute("Check whether lunch is confirmed")
    assert (result.success, result.response) == (True, "Nothing needed; Alice already confirmed.")
    assert history(agent.id) == [("agent_response", "Nothing needed; Alice already confirmed.")]


async def test_tool_call_runs_and_the_result_is_fed_back(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(calls(("gmail_create_draft", DRAFT_ARGS)), reply("Draft r-1 created"))
    result = await ExecutionAgentRuntime(agent.id).execute("Draft a lunch invite to Alice")

    assert result.tools_executed == ["gmail_create_draft"]
    assert fake_gmail.drafts["r-1"]["recipient_email"] == "alice@example.com"
    assert tool_results(fake_llm.calls[1]) == [{
        "tool": "gmail_create_draft", "status": "success", "arguments": DRAFT_ARGS,
        "result": {"successful": True, "data": {"id": "r-1"}},
    }]
    assert [tag for tag, _ in history(agent.id)] == ["tool_call", "agent_response"]
    assert tool_calls(agent.id) == [{
        "ref": "r1", "tool": "gmail_create_draft", "ok": True, "arguments": DRAFT_ARGS,
        "result": {"successful": True, "data": {"id": "r-1"}},
    }]


async def test_prompt_contains_identity_tools_and_past_history(fake_llm, fake_gmail, agent):
    logs = get_execution_agent_logs()
    logs.record_request(agent.id, "Draft a lunch invite")
    logs.record_agent_response(agent.id, "Draft r-1 created for alice@example.com")
    fake_llm.for_execution(reply("Sent."))

    await ExecutionAgentRuntime(agent.id).execute("Send it")

    call = fake_llm.calls[0]
    assert "Agent Name: Lunch with Alice" in call.system
    assert "# Execution History" in call.system and "Draft r-1 created" in call.system
    assert call.messages == [{"role": "user", "content": "Send it"}]
    assert {"gmail_create_draft", "task_email_search", "createTrigger"} <= set(call.tool_names)


async def test_worker_sees_its_own_facts_in_the_next_run(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(calls(("gmail_create_draft", DRAFT_ARGS)), reply("Drafted"), reply("ok"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    await ExecutionAgentRuntime(agent.id).execute("Anything open?")
    system = fake_llm.calls_to(is_execution_agent)[-1].system
    assert "- Open drafts: r-1" in system and "- Email addresses: alice@example.com" in system


async def test_long_lived_worker_prompt_stays_bounded_and_keeps_its_card(fake_llm, fake_gmail, agent):
    logs = get_execution_agent_logs()
    for i in range(300):
        logs.record_request(agent.id, f"Check for Alice's reply, round {i}")
        logs.record_agent_response(agent.id, f"Round {i}: no reply yet. " + "Details. " * 40)
    get_agent_registry().update_summary(agent.id, "Waiting on Alice's lunch reply; she prefers Rosa's.")
    fake_llm.for_execution(reply("ok"))

    await ExecutionAgentRuntime(agent.id).execute("Any reply?")

    system = fake_llm.calls_to(is_execution_agent)[0].system
    history = system.split("# Execution History", 1)[1]
    assert len(history) <= 4000 * 4 + 200
    assert "she prefers Rosa's" in system  # older runs live on through the card
    assert '<earlier_runs count="' in history and "round 299" in history and "round 0," not in history


async def test_new_agent_has_no_history_section(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(reply("ok"))
    await ExecutionAgentRuntime(agent.id).execute("hi")
    assert "# Execution History" not in fake_llm.calls[0].system


async def test_each_run_is_recorded_on_the_agent_card(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(reply("one"), reply("two"))
    await ExecutionAgentRuntime(agent.id).execute("first")
    await ExecutionAgentRuntime(agent.id).execute("second")
    assert get_agent_registry().get(agent.id).run_count == 2


async def test_result_carries_both_id_and_name(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(reply("ok"))
    result = await ExecutionAgentRuntime(agent.id).execute("hi")
    assert (result.agent_id, result.agent_name) == (agent.id, "Lunch with Alice")


def test_unknown_agent_id_is_rejected():
    with pytest.raises(ValueError, match="Unknown execution agent"):
        ExecutionAgentRuntime("a99999")


async def test_agents_with_the_same_name_keep_separate_memories(fake_llm, fake_gmail, agent):
    # P4's memory collision: names no longer decide where memory lives.
    twin = get_agent_registry().create("Lunch with Alice")
    fake_llm.for_execution(reply("first agent's answer"), reply("second agent's answer"))
    await ExecutionAgentRuntime(agent.id).execute("a")
    await ExecutionAgentRuntime(twin.id).execute("b")
    assert history(agent.id) == [("agent_response", "first agent's answer")]
    assert history(twin.id) == [("agent_response", "second agent's answer")]


# --- Tool failures --------------------------------------------------------

async def test_unknown_tool_is_reported_back_and_the_agent_continues(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(calls(("teleport", {})), reply("Could not do that"))
    result = await ExecutionAgentRuntime(agent.id).execute("go")
    assert result.success is True
    assert tool_results(fake_llm.calls[1])[0]["error"] == "Unknown tool: teleport"


async def test_crashing_tool_is_reported_back(fake_llm, fake_gmail, agent):
    fake_gmail.fail_with = RuntimeError("Composio timeout")
    fake_llm.for_execution(calls(("gmail_create_draft", DRAFT_ARGS)), reply("Gmail is down"))
    result = await ExecutionAgentRuntime(agent.id).execute("Draft it")
    assert result.success is True
    feedback = tool_results(fake_llm.calls[1])[0]
    assert (feedback["status"], feedback["error"]) == ("error", "Composio timeout")


async def test_missing_tool_arguments_are_reported_back(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(calls(("gmail_create_draft", {"subject": "no recipient"})), reply("retrying"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    feedback = tool_results(fake_llm.calls[1])[0]
    assert feedback["status"] == "error" and "recipient_email" in feedback["error"]
    assert fake_gmail.actions == []


async def test_gmail_not_connected_reaches_the_model(fake_llm, fake_gmail, agent):
    fake_gmail.connected = False
    fake_llm.for_execution(calls(("gmail_create_draft", DRAFT_ARGS)), reply("Gmail isn't connected"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    assert "Gmail not connected" in tool_results(fake_llm.calls[1])[0]["result"]["error"]
    assert fake_gmail.actions == []


# --- Whole-run failures ---------------------------------------------------

async def test_iteration_limit_fails_the_run_and_is_logged(fake_llm, fake_gmail, agent):
    limit = ExecutionAgentRuntime.MAX_TOOL_ITERATIONS
    fake_llm.for_execution(*[calls(("gmail_list_drafts", {}))] * limit)
    result = await ExecutionAgentRuntime(agent.id).execute("loop forever")
    assert result.success is False
    assert "iteration limit" in result.response
    assert history(agent.id)[-1][0] == "agent_response" and history(agent.id)[-1][1].startswith("Error:")


async def test_llm_outage_fails_the_run(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(OpenRouterError("429 rate limited"))
    result = await ExecutionAgentRuntime(agent.id).execute("go")
    assert (result.success, result.error) == (False, "429 rate limited")


# --- Memory fidelity (P6) and attribution (P17) ----------------------------

async def test_agent_memory_keeps_the_full_draft_it_wrote(fake_llm, fake_gmail, agent):
    body = "Hi Alice, " + "lots of detail about the plan. " * 20 + "THE IMPORTANT ENDING."
    fake_llm.for_execution(calls(("gmail_create_draft", {**DRAFT_ARGS, "body": body})), reply("Drafted"),
                           reply("Done"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    assert tool_calls(agent.id)[0]["arguments"]["body"] == body
    await ExecutionAgentRuntime(agent.id).execute("What did you write?")
    assert "THE IMPORTANT ENDING." in fake_llm.calls_to(is_execution_agent)[-1].system


async def test_agent_memory_keeps_full_tool_results(fake_llm, fake_gmail, agent):
    for i in range(5):
        fake_gmail.drafts[f"r-{i}"] = {"id": f"r-{i}", "subject": f"Draft number {i} " + "x" * 80}
    fake_llm.for_execution(calls(("gmail_list_drafts", {})), reply("Listed"))
    await ExecutionAgentRuntime(agent.id).execute("List my drafts")
    assert "Draft number 4" in json.dumps(tool_calls(agent.id)[0]["result"])


async def test_gmail_and_search_actions_are_logged_only_under_the_acting_agent(fake_llm, fake_gmail, agent):
    fake_gmail.add_email("m-1", subject="Lunch Friday?", sender="Alice <alice@example.com>", body="Noon?")
    fake_llm.for_execution(
        calls(("gmail_create_draft", DRAFT_ARGS)),
        calls(("task_email_search", {"search_query": "lunch with Alice"})),
        reply("Done"),
    )
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "from:alice"})),
        calls(("return_search_results", {"message_ids": ["m-1"]})),
    )
    await ExecutionAgentRuntime(agent.id).execute("Draft it, then find her lunch email")

    assert get_execution_agent_logs().list_agents() == [agent.id]
    assert [c["tool"] for c in tool_calls(agent.id)] == ["gmail_create_draft", "task_email_search"]


# --- Safety (P20) ----------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="P20: nothing in code stops an agent sending a draft the user never saw")
async def test_agent_cannot_send_a_draft_the_user_has_not_approved(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(
        calls(("gmail_create_draft", DRAFT_ARGS)),
        calls(("gmail_execute_draft", {"draft_id": "r-1"})),
        reply("Sent"),
    )
    await ExecutionAgentRuntime(agent.id).execute("Email Alice about lunch")
    assert fake_gmail.sent == []
