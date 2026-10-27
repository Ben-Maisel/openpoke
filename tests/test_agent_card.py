"""Tests for refreshing an agent's card summary after each run (P1d)."""

import asyncio

import pytest

from fakes import Gated, calls, is_card_refresh, reply, wait_until
from server.agents.execution_agent.card import MAX_SUMMARY_CHARS, refresh_card_summary
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.config import get_settings
from server.openrouter_client import OpenRouterError
from server.services import get_agent_registry

pytestmark = pytest.mark.usefixtures("clean_state")


@pytest.fixture
def agent():
    return get_agent_registry().create("Lunch with Alice", summary="Invite Alice Wong to lunch Friday")


def summary(agent_id):
    return get_agent_registry().get(agent_id).summary


# --- refresh_card_summary ---------------------------------------------------------

async def test_card_is_rewritten_from_previous_card_request_and_report(fake_llm, agent):
    fake_llm.script(reply("Lunch with Alice Wong, Fri noon: invite draft r-1 awaiting user approval."), when=is_card_refresh)
    new = await refresh_card_summary(agent.id, "Draft the invite", "Draft r-1 created for alice.wong@acme.com")

    assert new == summary(agent.id) == "Lunch with Alice Wong, Fri noon: invite draft r-1 awaiting user approval."
    call = fake_llm.calls[0]
    assert call.model == get_settings().summarizer_model
    assert call.tools is None
    for part in ("Worker name: Lunch with Alice", "Previous card: Invite Alice Wong to lunch Friday",
                 "Latest request: Draft the invite", "Worker's report: Draft r-1 created"):
        assert part in call.prompt


async def test_card_text_is_tidied_and_capped(fake_llm, agent):
    fake_llm.script(reply("  Line one\n\n  line   two  " + "x" * 1000), when=is_card_refresh)
    new = await refresh_card_summary(agent.id, "r", "s")
    assert new.startswith("Line one line two ")
    assert len(new) == MAX_SUMMARY_CHARS


@pytest.mark.parametrize("failure", [OpenRouterError("503"), reply("   "), {"choices": []}])
async def test_failed_or_empty_refresh_keeps_the_old_card(fake_llm, agent, failure):
    fake_llm.script(failure, when=is_card_refresh)
    assert await refresh_card_summary(agent.id, "r", "s") is None
    assert summary(agent.id) == "Invite Alice Wong to lunch Friday"


async def test_unknown_agent_is_skipped_without_an_llm_call(fake_llm):
    assert await refresh_card_summary("a99999", "r", "s") is None
    assert fake_llm.calls == []


# --- After a real run ----------------------------------------------------------------

async def test_successful_run_refreshes_the_card_in_the_background(fake_llm, fake_gmail, agent):
    card_call = Gated(reply("Invite draft r-1 for Alice Wong awaiting approval."))
    fake_llm.script(card_call, when=is_card_refresh)
    fake_llm.for_execution(calls(("gmail_create_draft", {"recipient_email": "alice.wong@acme.com",
                                                        "subject": "Lunch?", "body": "Fri?"})),
                           reply("Draft r-1 created"))

    result = await ExecutionAgentRuntime(agent.id).execute("Draft the invite")
    # The run has already reported back while the card refresh is still in flight.
    assert result.response == "Draft r-1 created"
    await wait_until(card_call.reached.is_set)
    assert summary(agent.id) == "Invite Alice Wong to lunch Friday"

    card_call.release()
    await wait_until(lambda: summary(agent.id) == "Invite draft r-1 for Alice Wong awaiting approval.")


async def test_failed_run_does_not_refresh_the_card(fake_llm, fake_gmail, agent):
    fake_llm.for_execution(OpenRouterError("500"))
    result = await ExecutionAgentRuntime(agent.id).execute("Draft the invite")
    assert result.success is False
    await asyncio.sleep(0.05)  # give any (wrongly) scheduled background refresh the chance to run
    assert fake_llm.calls_to(is_card_refresh) == []
