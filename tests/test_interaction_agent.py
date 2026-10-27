"""Component tests for the interaction agent loop, driven by a scripted fake LLM."""

import asyncio
import json
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
    agent = get_agent_registry().create("Lunch with Alice")

    await InteractionAgentRuntime().execute("first question")
    await InteractionAgentRuntime().execute("second question")

    prompt = fake_llm.calls[1].prompt
    assert "<conversation_history>" in prompt and "first question" in prompt and "first answer" in prompt
    assert f'<agent id="{agent.id}" name="Lunch with Alice" last_used=' in prompt
    assert prompt.rstrip().endswith("<new_user_message>\nsecond question\n</new_user_message>")
    # The new message must not also appear in the history block.
    history = prompt.split("</conversation_history>")[0]
    assert "second question" not in history


async def test_prompt_shows_a_bounded_working_set_not_the_whole_roster(fake_llm):
    # P2: however many agents exist, only the recent ones and the best matches are shown.
    fake_llm.for_interaction(reply("ok"))
    for i in range(50):
        get_agent_registry().create(f"Errand {i}")
    await InteractionAgentRuntime().execute("hi")
    prompt = fake_llm.calls[0].prompt
    assert prompt.count("<agent id=") == 5  # the hot cache; "hi" matches nothing
    assert '<other_agents count="45">' in prompt


async def test_an_older_agent_matching_the_message_is_shown_with_its_card(fake_llm):
    registry = get_agent_registry()
    lease = registry.create("Lease Renewal with Landlord", summary="Asked the landlord about renewing; awaiting reply")
    registry.add_facts(lease.id, "draft", ["r-9"])
    registry.add_facts(lease.id, "trigger", ["4"])
    for i in range(8):  # newer agents push the lease agent out of the hot cache
        registry.create(f"Errand {i}")
        registry.touch(registry.list_agents()[0].id)
    fake_llm.for_interaction(reply("ok"))
    await InteractionAgentRuntime().execute("did the landlord reply about the lease?")

    matching = fake_llm.calls[0].prompt.split("<matching_agents>")[1].split("</matching_agents>")[0]
    assert f'<agent id="{lease.id}" name="Lease Renewal with Landlord"' in matching
    assert "Asked the landlord about renewing; awaiting reply Open drafts: r-9. Live reminders: 4." in matching


async def test_search_agents_tool_finds_agents_that_are_not_shown(fake_llm):
    registry = get_agent_registry()
    lease = registry.create("Lease Renewal with Landlord", summary="Awaiting landlord reply")
    fake_llm.for_interaction(calls(("search_agents", {"query": "landlord lease"})), reply("found it"))
    await InteractionAgentRuntime().execute("hmm")
    found = json.loads(tool_results(fake_llm.calls[1])[0])["result"]
    assert [a["agent_id"] for a in found["agents"]] == [lease.id]
    assert found["agents"][0]["summary"] == "Awaiting landlord reply" and found["total_agents"] == 1


async def test_search_agents_tool_says_when_nothing_matches(fake_llm):
    get_agent_registry().create("Lease Renewal with Landlord")
    fake_llm.for_interaction(calls(("search_agents", {"query": "denver flights"})), reply("nothing"))
    await InteractionAgentRuntime().execute("hmm")
    found = json.loads(tool_results(fake_llm.calls[1])[0])["result"]
    assert found["agents"] == [] and "create_agent" in found["note"]


async def test_agent_names_are_escaped_in_the_prompt(fake_llm):
    fake_llm.for_interaction(reply("ok"))
    agent = get_agent_registry().create('Bob "the <builder>"')
    await InteractionAgentRuntime().execute("hi")
    assert f'<agent id="{agent.id}" name="Bob &quot;the &lt;builder&gt;&quot;"' in fake_llm.calls[0].prompt


async def test_agent_reports_arrive_as_agent_messages(fake_llm):
    fake_llm.for_interaction(calls(("send_message_to_user", {"message": "draft is ready"})), reply(""))
    await InteractionAgentRuntime().handle_agent_message("[SUCCESS] Lunch with Alice: draft r-1 created")
    assert "<new_agent_message>" in fake_llm.calls[0].prompt
    assert log_tags() == ["agent_message", "poke_reply"]
    assert chat() == [("assistant", "draft is ready")]


# --- Delegating to execution agents ---------------------------------------

def create(name, instructions, **extra):
    return calls(("create_agent", {"name": name, "instructions": instructions, **extra}))


def send(agent_id, instructions):
    return calls(("send_message_to_agent", {"agent_id": agent_id, "instructions": instructions}))


async def test_creating_registers_the_agent_logs_the_request_and_dispatches_by_id(fake_llm, dispatcher):
    fake_llm.for_interaction(create("Lunch with Alice", "Draft an invite"), reply(""))
    await InteractionAgentRuntime().execute("invite alice to lunch")
    await asyncio.sleep(0)  # let the background dispatch run

    (agent,) = get_agent_registry().list_agents()
    assert agent.name == "Lunch with Alice"
    assert agent.summary == "Draft an invite"  # the card starts as the creating instructions
    assert [p for _, _, p in get_execution_agent_logs().iter_entries(agent.id)] == ["Draft an invite"]
    assert dispatcher.dispatched == [(agent.id, "Draft an invite")]
    result = json.loads(tool_results(fake_llm.calls[1])[0])["result"]
    assert (result["agent_id"], result["new_agent_created"]) == (agent.id, True)


async def test_sending_by_id_reuses_the_existing_agent(fake_llm, dispatcher):
    agent = get_agent_registry().create("Lunch with Alice")
    fake_llm.for_interaction(send(agent.id, "Send it"), reply(""))
    await InteractionAgentRuntime().execute("send it")
    await asyncio.sleep(0)
    assert agent_names() == ["Lunch with Alice"]
    assert dispatcher.dispatched == [(agent.id, "Send it")]


async def test_unknown_id_is_an_error_and_creates_nothing(fake_llm, dispatcher):
    agent = get_agent_registry().create("Lunch with Alice")
    fake_llm.for_interaction(send("a99999", "Send it"), send(agent.id, "Send it"), reply(""))
    await InteractionAgentRuntime().execute("send it")
    await asyncio.sleep(0)

    feedback = json.loads(tool_results(fake_llm.calls[1])[0])
    assert feedback["status"] == "error" and "No agent with id 'a99999'" in feedback["error"]["error"]
    assert feedback["error"]["recently_used_agents"] == [{"agent_id": agent.id, "name": "Lunch with Alice"}]
    assert agent_names() == ["Lunch with Alice"]  # nothing new was created
    assert dispatcher.dispatched == [(agent.id, "Send it")]  # the model corrected itself


async def test_likely_duplicate_is_held_back_with_the_matching_agent(fake_llm, dispatcher):
    existing = get_agent_registry().create("Lunch with Alice Wong", summary="Invite sent, awaiting reply")
    fake_llm.for_interaction(create("Alice Wong Lunch", "Did she reply?"), send(existing.id, "Did she reply?"), reply(""))
    await InteractionAgentRuntime().execute("did alice wong reply about lunch?")
    await asyncio.sleep(0)

    warning = json.loads(tool_results(fake_llm.calls[1])[0])["result"]
    assert (warning["status"], warning["created"]) == ("possible_duplicate", False)
    assert warning["similar_agents"] == [{"agent_id": existing.id, "name": "Lunch with Alice Wong",
                                          "summary": "Invite sent, awaiting reply"}]
    assert agent_names() == ["Lunch with Alice Wong"]
    assert dispatcher.dispatched == [(existing.id, "Did she reply?")]


async def test_confirm_new_creates_despite_a_lookalike(fake_llm, dispatcher):
    get_agent_registry().create("Lunch with Alice Chen")
    fake_llm.for_interaction(create("Lunch with Alice Wong", "Invite Alice Wong", confirm_new=True), reply(""))
    await InteractionAgentRuntime().execute("invite alice wong to lunch too")
    await asyncio.sleep(0)
    assert sorted(agent_names()) == ["Lunch with Alice Chen", "Lunch with Alice Wong"]


async def test_genuinely_new_task_is_created_without_a_warning(fake_llm, dispatcher):
    get_agent_registry().create("Lunch with Alice Wong")
    fake_llm.for_interaction(create("Denver Flights", "Find flights"), reply(""))
    await InteractionAgentRuntime().execute("find me flights to denver")
    await asyncio.sleep(0)
    assert sorted(agent_names()) == ["Denver Flights", "Lunch with Alice Wong"]


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
