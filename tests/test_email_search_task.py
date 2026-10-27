"""Tests for the email search task: a mini-agent with its own LLM loop over Gmail."""

import pytest

from fakes import calls, reply
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.agents.execution_agent.tasks.search_email.tool import MAX_LLM_ITERATIONS, task_email_search
from server.services import get_agent_registry

pytestmark = pytest.mark.usefixtures("clean_state")


@pytest.fixture
def inbox(fake_gmail):
    fake_gmail.add_email("m-1", subject="Lunch Friday?", sender="Alice <alice@example.com>", body="Noon at Rosa's?")
    fake_gmail.add_email("m-2", subject="Invoice", sender="Billing <billing@example.com>", body="Amount due: $40")
    return fake_gmail


async def test_search_runs_queries_and_returns_the_selected_emails(fake_llm, inbox):
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "from:alice"})),
        calls(("return_search_results", {"message_ids": ["m-1"]})),
    )
    results = await task_email_search("emails from Alice about lunch")

    assert [r["id"] for r in results] == ["m-1"]
    assert results[0]["clean_text"] == "Noon at Rosa's?"
    assert results[0]["query"] == "from:alice"
    (action, args), = inbox.actions
    assert action == "GMAIL_FETCH_EMAILS"
    assert (args["query"], args["include_payload"], args["verbose"]) == ("from:alice", True, True)


async def test_search_model_sees_the_request_and_the_results(fake_llm, inbox):
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "from:alice"})),
        calls(("return_search_results", {"message_ids": ["m-1"]})),
    )
    await task_email_search("emails from Alice")
    assert fake_llm.calls[0].prompt == "Please help me find emails: emails from Alice"
    assert "Noon at Rosa" in fake_llm.calls[1].messages[-1]["content"]


async def test_unknown_or_blank_selected_ids_are_ignored(fake_llm, inbox):
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "lunch"})),
        calls(("return_search_results", {"message_ids": ["m-1", "m-999", "  "]})),
    )
    assert [r["id"] for r in await task_email_search("lunch")] == ["m-1"]


async def test_model_stopping_without_selecting_returns_nothing(fake_llm, inbox):
    # Characterises current behaviour: found emails are discarded unless the
    # model explicitly calls return_search_results.
    fake_llm.for_search(calls(("gmail_fetch_emails", {"query": "lunch"})), reply("Found it!"))
    assert await task_email_search("lunch") == []


@pytest.mark.parametrize("query", ["", "   "])
async def test_blank_request_is_rejected_without_calling_the_llm(fake_llm, fake_gmail, query):
    assert await task_email_search(query) == {"error": "search_query must not be empty"}
    assert fake_llm.calls == []


async def test_not_connected_is_rejected_without_calling_the_llm(fake_llm, fake_gmail):
    fake_gmail.connected = False
    assert "Gmail not connected" in (await task_email_search("lunch"))["error"]
    assert fake_llm.calls == []


async def test_gmail_failure_is_reported_to_the_search_model(fake_llm, inbox):
    inbox.fail_with = RuntimeError("Composio timeout")
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "lunch"})),
        calls(("return_search_results", {"message_ids": []})),
    )
    assert await task_email_search("lunch") == []
    assert "Composio timeout" in fake_llm.calls[1].messages[-1]["content"]


async def test_iteration_limit_returns_an_error(fake_llm, inbox):
    fake_llm.for_search(*[calls(("gmail_fetch_emails", {"query": "lunch"}))] * MAX_LLM_ITERATIONS)
    result = await task_email_search("lunch")
    assert "iteration limit" in result["error"]


async def test_execution_agent_can_use_search_as_a_tool(fake_llm, inbox):
    fake_llm.for_execution(calls(("task_email_search", {"search_query": "lunch with Alice"})), reply("Alice suggested Rosa's"))
    fake_llm.for_search(
        calls(("gmail_fetch_emails", {"query": "from:alice"})),
        calls(("return_search_results", {"message_ids": ["m-1"]})),
    )
    agent = get_agent_registry().create("Lunch with Alice")
    result = await ExecutionAgentRuntime(agent.id).execute("Where did Alice want to eat?")
    assert result.response == "Alice suggested Rosa's"
    assert "Noon at Rosa" in fake_llm.calls_to(lambda c: "gmail_create_draft" in c.tool_names)[1].messages[-1]["content"]
