"""End-to-end flows through the whole agent stack, with a scripted LLM and fake Gmail.

The interaction agent, batch manager, execution agents and tools are all real;
only the model and Gmail are faked.
"""

import asyncio
import re

import pytest

from fakes import Gated, calls, is_agent, is_interaction_agent, reply, wait_until
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.openrouter_client import OpenRouterError
from server.services import get_agent_registry, get_conversation_log, get_draft_store

pytestmark = pytest.mark.usefixtures("clean_state", "batch_manager")

AGENT = "Lunch with Alice"
DRAFT = {"recipient_email": "alice@example.com", "subject": "Lunch Friday?", "body": "Are you free at noon?"}


def user_turn(call):
    return is_interaction_agent(call) and "<new_user_message>" in call.prompt


def agent_turn(call):
    return is_interaction_agent(call) and "<new_agent_message>" in call.prompt


def create(name, instructions):
    return calls(("create_agent", {"name": name, "instructions": instructions}))


def send(agent_id, instructions):
    return calls(("send_message_to_agent", {"agent_id": agent_id, "instructions": instructions}))


def chat():
    return [(m.role, m.content) for m in get_conversation_log().to_chat_messages()]


def agent_reports():
    return [p for tag, _, p in get_conversation_log().iter_entries() if tag == "agent_message"]


def without_ids(report):
    """Reports name each agent with its id, e.g. "[SUCCESS] Alice (a7): ..."; ids differ per test run."""
    return re.sub(r" \(a\d+\):", ":", report)


# --- The headline flow ------------------------------------------------------

async def test_draft_confirm_and_send_through_the_same_agent(fake_llm, fake_gmail):
    fake_llm.script(
        calls(("send_message_to_user", {"message": "on it"})), create(AGENT, "Draft a lunch invite to alice@example.com"), reply(""),
        when=user_turn,
    )
    fake_llm.script(
        calls(("send_draft", {"draft_id": "r-1"}),
              ("send_message_to_user", {"message": "want me to send it?"})), reply(""),
        calls(("send_message_to_user", {"message": "sent!"})), reply(""),
        when=agent_turn,
    )
    fake_llm.script(
        calls(("gmail_create_draft", DRAFT)), reply("Draft r-1 created for alice@example.com"),
        calls(("gmail_execute_draft", {"draft_id": "r-1"})), reply("Sent draft r-1"),
        when=is_agent(AGENT),
    )

    await InteractionAgentRuntime().execute("invite alice to lunch friday")
    await wait_until(lambda: ("assistant", "want me to send it?") in chat())
    assert fake_gmail.sent == []  # nothing leaves before the user approves

    # The report carried the agent's id into the history, so the model can route "send it" straight back.
    (agent,) = get_agent_registry().list_agents()
    assert f"{AGENT} ({agent.id})" in agent_reports()[0]
    fake_llm.script(
        calls(("approve_draft", {"draft_id": "r-1"}),
              ("send_message_to_agent", {"agent_id": agent.id, "instructions": "User approved. Send draft r-1."})),
        reply(""),
        when=user_turn,
    )
    await InteractionAgentRuntime().execute("send it")
    await wait_until(lambda: ("assistant", "sent!") in chat())
    draft = get_draft_store().get("r-1")
    assert (draft.status, draft.approval_text) == ("sent", "send it")

    assert chat() == [
        ("user", "invite alice to lunch friday"),
        ("assistant", "on it"),
        ("draft", "Draft r-1\nTo: alice@example.com\nSubject: Lunch Friday?\n\nAre you free at noon?"),
        ("assistant", "want me to send it?"),
        ("user", "send it"),
        ("assistant", "sent!"),
    ]
    assert [m["recipient_email"] for m in fake_gmail.sent] == ["alice@example.com"]
    (agent,) = get_agent_registry().list_agents()
    assert (agent.name, agent.run_count) == (AGENT, 2)  # one agent, used for both runs
    # The second run of the agent remembered the first: that is what made "send it" work.
    second_run_prompt = fake_llm.calls_to(is_agent(AGENT))[2].system
    assert "Draft r-1 created" in second_run_prompt
    assert [without_ids(r) for r in agent_reports()] == [
        f"[SUCCESS] {AGENT}: Draft r-1 created for alice@example.com", f"[SUCCESS] {AGENT}: Sent draft r-1"]


# --- Batching ---------------------------------------------------------------

async def test_agents_dispatched_together_report_back_in_one_message(fake_llm, fake_gmail):
    fake_llm.script(calls(("create_agent", {"name": "Alice", "instructions": "a"}),
                          ("create_agent", {"name": "Bob", "instructions": "b"})), reply(""), when=user_turn)
    fake_llm.script(reply("Alice done"), when=is_agent("Alice"))
    fake_llm.script(reply("Bob done"), when=is_agent("Bob"))
    fake_llm.script(calls(("send_message_to_user", {"message": "both done"})), reply(""), when=agent_turn)

    await InteractionAgentRuntime().execute("invite alice and bob")
    await wait_until(lambda: ("assistant", "both done") in chat())

    (report,) = agent_reports()
    assert sorted(without_ids(line) for line in report.splitlines()) == ["[SUCCESS] Alice: Alice done", "[SUCCESS] Bob: Bob done"]


async def test_one_failing_agent_does_not_block_the_others(fake_llm, fake_gmail):
    fake_llm.script(calls(("create_agent", {"name": "Alice", "instructions": "a"}),
                          ("create_agent", {"name": "Bob", "instructions": "b"})), reply(""), when=user_turn)
    fake_llm.script(OpenRouterError("500 boom"), when=is_agent("Alice"))
    fake_llm.script(reply("Bob done"), when=is_agent("Bob"))
    fake_llm.script(reply(""), when=agent_turn)

    await InteractionAgentRuntime().execute("invite alice and bob")
    await wait_until(lambda: len(agent_reports()) == 1)

    lines = sorted(without_ids(line) for line in agent_reports()[0].splitlines())
    assert lines == ["[FAILED] Alice: Failed to complete task: 500 boom", "[SUCCESS] Bob: Bob done"]


async def test_stuck_agent_times_out_with_a_clear_failure(fake_llm, fake_gmail, batch_manager):
    batch_manager.timeout_seconds = 0.05
    stuck = Gated(reply("never arrives"))
    fake_llm.script(create("Alice", "a"), reply(""), when=user_turn)
    fake_llm.script(stuck, when=is_agent("Alice"))
    fake_llm.script(reply(""), when=agent_turn)

    await InteractionAgentRuntime().execute("invite alice")
    await wait_until(lambda: len(agent_reports()) == 1)
    assert [without_ids(r) for r in agent_reports()] == ["[FAILED] Alice: Execution timed out after 0.05 seconds"]
    stuck.release()


# --- Concurrency problems ---------------------------------------------------

@pytest.mark.xfail(strict=True, reason="P9: one global batch holds a fast result until an unrelated slow task finishes")
async def test_fast_result_is_not_held_back_by_an_unrelated_slow_task(fake_llm, fake_gmail, batch_manager):
    slow = Gated(reply("Slow research done"))
    fake_llm.script(create("Research", "dig deep"), reply(""), create("Quick", "quick one"), reply(""), when=user_turn)
    fake_llm.script(slow, when=is_agent("Research"))
    fake_llm.script(reply("Fast answer"), when=is_agent("Quick"))
    fake_llm.script(reply(""), reply(""), when=agent_turn)

    await InteractionAgentRuntime().execute("research the market")
    await wait_until(slow.reached.is_set)
    await InteractionAgentRuntime().execute("quick question")
    try:
        await wait_until(lambda: any("Fast answer" in r for r in agent_reports()), timeout=0.5)
    finally:
        slow.release()
        await wait_until(lambda: batch_manager._batch_state is None)


@pytest.mark.xfail(strict=True, reason="P10: two runs of the same agent execute concurrently on the same memory")
async def test_second_message_to_a_busy_agent_sees_the_first_runs_result(fake_llm, fake_gmail, batch_manager):
    first = Gated(reply("Draft r-1 created"))
    fake_llm.script(first, reply("Sent r-1"), when=is_agent(AGENT))
    fake_llm.script(reply(""), reply(""), when=agent_turn)

    agent = get_agent_registry().create(AGENT)
    run_1 = asyncio.create_task(batch_manager.execute_agent(agent.id, "Draft the invite"))
    await wait_until(first.reached.is_set)
    run_2 = asyncio.create_task(batch_manager.execute_agent(agent.id, "Send it"))
    await asyncio.sleep(0.05)  # if runs were serialised, run 2 would still be waiting here
    first.release()
    await asyncio.wait_for(asyncio.gather(run_1, run_2), timeout=2)

    second_run_prompt = fake_llm.calls_to(is_agent(AGENT))[1].system
    assert "Draft r-1 created" in second_run_prompt


@pytest.mark.xfail(strict=True, reason="P11: interaction-agent turns run concurrently and read stale history")
async def test_agent_report_turn_sees_the_reply_from_a_turn_already_in_progress(fake_llm):
    in_progress = Gated(calls(("send_message_to_user", {"message": "hello!"})))
    fake_llm.script(in_progress, reply(""), when=user_turn)
    fake_llm.script(reply(""), when=agent_turn)

    user_task = asyncio.create_task(InteractionAgentRuntime().execute("hi"))
    await wait_until(in_progress.reached.is_set)
    report_task = asyncio.create_task(InteractionAgentRuntime().handle_agent_message("[SUCCESS] X: done"))
    await asyncio.sleep(0.05)
    in_progress.release()
    await asyncio.wait_for(asyncio.gather(user_task, report_task), timeout=2)

    report_prompt = fake_llm.calls_to(agent_turn)[0].prompt
    assert "hello!" in report_prompt
