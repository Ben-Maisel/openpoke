"""Component tests for the interaction agent loop, driven by a scripted fake LLM."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from fakes import calls, reply
from server.agents.interaction_agent import runtime as runtime_module
from server.agents.interaction_agent import tools as tools_module
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.app import app
from server.config import Settings
from server.openrouter_client import OpenRouterError
from server.services import get_agent_registry, get_conversation_log, get_execution_agent_logs

pytestmark = pytest.mark.usefixtures("clean_state")


class RecordingDispatcher:
    """Stands in for the execution batch manager so 4a tests only exercise the interaction agent."""

    def __init__(self):
        self.dispatched = []

    async def execute_agent(self, agent_id, instructions, request_id=None):
        self.dispatched.append((agent_id, instructions))


def agent_names():
    return [a.name for a in get_agent_registry().list_agents()]


@pytest.fixture
def dispatcher(monkeypatch):
    fake = RecordingDispatcher()
    monkeypatch.setattr(tools_module, "_EXECUTION_BATCH_MANAGER", fake)
    return fake


def chat():
    return [(m.role, m.content) for m in get_conversation_log().to_chat_messages()]


def log_tags():
    return [tag for tag, _, _ in get_conversation_log().iter_entries()]


def tool_results(call):
    """Tool-result messages the model was shown in a given request."""
    return [m["content"] for m in call.messages if m["role"] == "tool"]


# --- Basic replies --------------------------------------------------------

async def test_plain_text_answer_is_recorded_as_the_reply(fake_llm):
    fake_llm.for_interaction(reply("hey! what's up"))
    result = await InteractionAgentRuntime().execute("hi")
    assert (result.success, result.response) == (True, "hey! what's up")
    assert chat() == [("user", "hi"), ("assistant", "hey! what's up")]


async def test_send_message_to_user_is_the_reply_and_closing_text_stays_private(fake_llm):
    fake_llm.for_interaction(
        calls(("send_message_to_user", {"message": "on it"})),
        reply("(internal note: told the user)"),
    )
    result = await InteractionAgentRuntime().execute("check my email")
    assert result.response == "on it"
    assert chat() == [("user", "check my email"), ("assistant", "on it")]


async def test_wait_is_logged_but_never_shown(fake_llm):
    fake_llm.for_interaction(calls(("wait", {"reason": "already answered"})), reply(""))
    await InteractionAgentRuntime().execute("hello again")
    assert "wait" in log_tags()
    assert chat() == [("user", "hello again")]


async def test_multiple_tool_calls_in_one_turn_all_run_in_order(fake_llm):
    fake_llm.for_interaction(
        calls(("send_message_to_user", {"message": "first"}), ("send_message_to_user", {"message": "second"})),
        reply(""),
    )
    result = await InteractionAgentRuntime().execute("go")
    assert chat()[1:] == [("assistant", "first"), ("assistant", "second")]
    assert result.response == "second"


# --- What the model is shown ----------------------------------------------

async def test_prompt_contains_history_roster_and_new_message(fake_llm):
    fake_llm.for_interaction(reply("first answer"), reply("second answer"))
    get_agent_registry().create("Lunch with Alice")

    await InteractionAgentRuntime().execute("first question")
    await InteractionAgentRuntime().execute("second question")

    prompt = fake_llm.calls[1].prompt
    assert "<conversation_history>" in prompt and "first question" in prompt and "first answer" in prompt
    assert '<agent name="Lunch with Alice" />' in prompt
    assert prompt.rstrip().endswith("<new_user_message>\nsecond question\n</new_user_message>")
    # The new message must not also appear in the history block.
    history = prompt.split("</conversation_history>")[0]
    assert "second question" not in history


async def test_prompt_lists_every_roster_agent(fake_llm):
    # Characterises today's behaviour (P2): the whole roster goes into every prompt.
    fake_llm.for_interaction(reply("ok"))
    for i in range(50):
        get_agent_registry().create(f"Agent {i}")
    await InteractionAgentRuntime().execute("hi")
    assert fake_llm.calls[0].prompt.count("<agent name=") == 50


async def test_agent_names_are_escaped_in_the_prompt(fake_llm):
    fake_llm.for_interaction(reply("ok"))
    get_agent_registry().create('Bob "the <builder>"')
    await InteractionAgentRuntime().execute("hi")
    assert '<agent name="Bob &quot;the &lt;builder&gt;&quot;" />' in fake_llm.calls[0].prompt


async def test_agent_reports_arrive_as_agent_messages(fake_llm):
    fake_llm.for_interaction(calls(("send_message_to_user", {"message": "draft is ready"})), reply(""))
    await InteractionAgentRuntime().handle_agent_message("[SUCCESS] Lunch with Alice: draft r-1 created")
    assert "<new_agent_message>" in fake_llm.calls[0].prompt
    assert log_tags() == ["agent_message", "poke_reply"]
    assert chat() == [("assistant", "draft is ready")]


# --- Delegating to execution agents ---------------------------------------

async def test_delegating_creates_agent_logs_request_and_dispatches(fake_llm, dispatcher):
    fake_llm.for_interaction(
        calls(("send_message_to_agent", {"agent_name": "Lunch with Alice", "instructions": "Draft an invite"})),
        reply(""),
    )
    await InteractionAgentRuntime().execute("invite alice to lunch")
    await asyncio.sleep(0)  # let the background dispatch run

    (agent,) = get_agent_registry().list_agents()
    assert agent.name == "Lunch with Alice"
    assert agent.summary == "Draft an invite"  # the card starts as the creating instructions
    # Memory and dispatch use the stable ID, not the name.
    assert [p for _, _, p in get_execution_agent_logs().iter_entries(agent.id)] == ["Draft an invite"]
    assert dispatcher.dispatched == [(agent.id, "Draft an invite")]
    assert '"new_agent_created": true' in tool_results(fake_llm.calls[1])[0]


async def test_same_name_reuses_the_existing_agent(fake_llm, dispatcher):
    fake_llm.for_interaction(
        calls(("send_message_to_agent", {"agent_name": "Lunch with Alice", "instructions": "Draft"})), reply(""),
        calls(("send_message_to_agent", {"agent_name": "Lunch with Alice", "instructions": "Send it"})), reply(""),
    )
    await InteractionAgentRuntime().execute("invite alice")
    await InteractionAgentRuntime().execute("send it")
    assert agent_names() == ["Lunch with Alice"]
    assert '"new_agent_created": false' in tool_results(fake_llm.calls[3])[0]
    assert dispatcher.dispatched[0][0] == dispatcher.dispatched[1][0]  # same agent ID both times


# --- Bad output from the model --------------------------------------------

@pytest.mark.parametrize(
    "bad_call, expected_error",
    [
        (("send_message_to_user", '{"message": "unterminated'), "invalid json"),
        (("send_message_to_user", '["not", "an", "object"]'), "not an object"),
        (("launch_rockets", {}), "Unknown tool"),
        (("send_message_to_user", {}), "Missing required arguments"),
        (("send_message_to_user", {"message": "hi", "extra": 1}), "Missing required arguments"),
    ],
)
async def test_bad_tool_calls_are_reported_back_to_the_model(fake_llm, bad_call, expected_error):
    fake_llm.for_interaction(calls(bad_call), reply("recovered"))
    result = await InteractionAgentRuntime().execute("hi")
    assert result.success is True
    error_message = tool_results(fake_llm.calls[1])[0]
    assert '"status": "error"' in error_message and expected_error in error_message
    assert chat() == [("user", "hi"), ("assistant", "recovered")]


# --- Failures --------------------------------------------------------------

async def test_stops_after_max_tool_iterations(fake_llm):
    limit = InteractionAgentRuntime.MAX_TOOL_ITERATIONS
    fake_llm.for_interaction(*[calls(("wait", {"reason": "loop"}))] * limit)
    result = await InteractionAgentRuntime().execute("hi")
    assert result.success is False
    assert "iteration limit" in result.error
    assert len(fake_llm.calls) == limit


async def test_llm_outage_returns_failure(fake_llm):
    fake_llm.for_interaction(OpenRouterError("503 upstream unavailable"))
    result = await InteractionAgentRuntime().execute("hi")
    assert (result.success, result.response) == (False, "")
    assert "503" in result.error


async def test_malformed_llm_response_returns_failure(fake_llm):
    fake_llm.for_interaction({"choices": []})
    result = await InteractionAgentRuntime().execute("hi")
    assert result.success is False


@pytest.mark.xfail(strict=True, reason="P12: when the interaction agent fails, the user is told nothing")
async def test_user_is_told_when_the_assistant_fails(fake_llm):
    fake_llm.for_interaction(OpenRouterError("503 upstream unavailable"))
    await InteractionAgentRuntime().execute("hi")
    assert chat()[-1][0] == "assistant"


@pytest.mark.xfail(strict=True, reason="P12: hitting the iteration limit leaves the user with no reply")
async def test_user_is_told_when_the_iteration_limit_is_hit(fake_llm):
    fake_llm.for_interaction(*[calls(("wait", {"reason": "loop"}))] * InteractionAgentRuntime.MAX_TOOL_ITERATIONS)
    await InteractionAgentRuntime().execute("hi")
    assert chat()[-1][0] == "assistant"


def test_missing_api_key_is_rejected_up_front(monkeypatch):
    monkeypatch.setattr(runtime_module, "get_settings", lambda: Settings(openrouter_api_key=None))
    with pytest.raises(ValueError, match="API key"):
        InteractionAgentRuntime()


# --- Drafts ----------------------------------------------------------------

async def test_draft_is_shown_with_recipient_subject_and_body(fake_llm):
    fake_llm.for_interaction(
        calls(("send_draft", {"to": "alice@example.com", "subject": "Lunch?", "body": "Friday at noon?"}),
              ("send_message_to_user", {"message": "want me to send it?"})),
        reply(""),
    )
    await InteractionAgentRuntime().handle_agent_message("[SUCCESS] Lunch: draft created")
    assert chat() == [
        ("assistant", "To: alice@example.com\nSubject: Lunch?\n\nFriday at noon?"),
        ("assistant", "want me to send it?"),
    ]


@pytest.mark.xfail(strict=True, reason="P16: drafts are recorded as ordinary replies, so the UI's draft bubble never shows")
async def test_draft_is_marked_as_a_draft_for_the_ui(fake_llm):
    fake_llm.for_interaction(
        calls(("send_draft", {"to": "alice@example.com", "subject": "Lunch?", "body": "Friday?"})), reply(""),
    )
    await InteractionAgentRuntime().handle_agent_message("[SUCCESS] Lunch: draft created")
    assert any(role == "draft" for role, _ in chat())


# --- Through the HTTP API, as the browser uses it --------------------------

def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_chat_api_accepts_message_and_reply_appears_in_history(fake_llm):
    fake_llm.for_interaction(reply("hello from the fake model"))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", json={"messages": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 202
        assert _wait_for(lambda: len(client.get("/api/v1/chat/history").json()["messages"]) == 2)
        history = client.get("/api/v1/chat/history").json()["messages"]
    assert [(m["role"], m["content"]) for m in history] == [("user", "hi"), ("assistant", "hello from the fake model")]


@pytest.mark.parametrize("body", [{"messages": []}, {"messages": [{"role": "assistant", "content": "x"}]},
                                  {"messages": [{"role": "user", "content": "   "}]}])
def test_chat_api_rejects_requests_without_a_user_message(body):
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", json=body)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_chat_api_reports_missing_api_key(monkeypatch):
    monkeypatch.setattr(runtime_module, "get_settings", lambda: Settings(openrouter_api_key=None))
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/send", json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 400
    assert "API key" in response.json()["error"]


def test_clearing_history_wipes_chat_agents_and_agent_logs(fake_llm, dispatcher):
    get_conversation_log().record_user_message("hi")
    agent = get_agent_registry().create("Lunch with Alice")
    get_execution_agent_logs().record_request(agent.id, "x")
    with TestClient(app) as client:
        assert client.delete("/api/v1/chat/history").status_code == 200
    assert chat() == []
    assert agent_names() == []
    assert get_execution_agent_logs().list_agents() == []
