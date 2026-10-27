"""Tests for the routing eval itself: an evaluator with a bug produces confident wrong numbers."""

import pytest

from fakes import calls, reply
from evals.routing.cases import Expectation, RoutingCase
from evals.routing.harness import render_history, run_decision
from evals.routing.scoring import CaseResult, Outcome, classify, is_near_miss, summarize, wilson_interval
from server.openrouter_client import OpenRouterError
from server.services.conversation.log import ConversationLog
from server.services.conversation.summarization.working_memory_log import WorkingMemoryLog

ROSTER = ["Lunch with Alice", "Vercel Job Offer", "Dentist Reminder"]
LUNCH = Expectation("agent", ("Lunch with Alice",))
NEW = Expectation("new")
NONE = Expectation("none")


def delegate(*names):
    return calls(*[("send_message_to_agent", {"agent_name": n, "instructions": "do it"}) for n in names])


def case(message="did alice reply?", expect=LUNCH, history=()):
    return RoutingCase(id="c1", category="explicit", message=message, expect=expect,
                       required_agents=("Lunch with Alice",), history=tuple(history))


# --- Expectation validation ---------------------------------------------------

def test_agent_expectation_requires_agents():
    with pytest.raises(ValueError):
        Expectation("agent")
    with pytest.raises(ValueError):
        Expectation("new", ("Lunch with Alice",))


# --- classify -----------------------------------------------------------------

@pytest.mark.parametrize(
    "expect, routes, outcome",
    [
        (LUNCH, ["Lunch with Alice"], Outcome.CORRECT),
        (LUNCH, ["Vercel Job Offer"], Outcome.WRONG_AGENT),
        (LUNCH, ["Lunch with Alice", "Vercel Job Offer"], Outcome.WRONG_AGENT),
        (LUNCH, ["Lunch with Alice", "Brand New Helper"], Outcome.CORRECT),  # extra parallel new agent is tolerated
        (LUNCH, ["Alice Lunch Invite"], Outcome.DUPLICATE),
        (LUNCH, ["lunch with alice"], Outcome.DUPLICATE),  # the app matches names exactly
        (LUNCH, [], Outcome.NO_DELEGATION),
        (NEW, ["Flight to Denver"], Outcome.CORRECT),
        (NEW, ["Dentist Reminder"], Outcome.WRONGLY_REUSED),
        (NEW, [], Outcome.NO_DELEGATION),
        (NONE, [], Outcome.CORRECT),
        (NONE, ["Lunch with Alice"], Outcome.UNNEEDED_DELEGATION),
    ],
)
def test_classify(expect, routes, outcome):
    assert classify(expect, routes, ROSTER) is outcome


def test_ambiguous_case_accepts_any_listed_agent():
    expect = Expectation("agent", ("Lunch with Alice", "Dentist Reminder"))
    assert classify(expect, ["Dentist Reminder"], ROSTER) is Outcome.CORRECT


def test_api_error_is_its_own_outcome():
    assert classify(LUNCH, [], ROSTER, error="timeout") is Outcome.ERROR


@pytest.mark.parametrize(
    "route, expected",
    [("lunch with alice", True), ("Lunch-With-Alice!", True), ("Lunch with Alice", False), ("Alice Lunch", False)],
)
def test_near_miss_detection(route, expected):
    assert is_near_miss(route, ROSTER) is expected


# --- statistics ---------------------------------------------------------------

def test_wilson_interval_known_values():
    low, high = wilson_interval(8, 10)
    assert (round(low, 3), round(high, 3)) == (0.490, 0.943)
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert wilson_interval(10, 10)[1] == 1.0


def test_summarize_groups_and_excludes_errors_from_accuracy():
    def result(size, outcome, cost=0.01, tokens=100, near_miss=False):
        return CaseResult("c", "explicit", size, outcome, [], near_miss, tokens, 5, cost)

    results = [
        result(10, Outcome.CORRECT), result(10, Outcome.CORRECT), result(10, Outcome.DUPLICATE, near_miss=True),
        result(10, Outcome.ERROR, cost=0.0, tokens=0),
        result(500, Outcome.WRONG_AGENT, tokens=9000),
    ]
    by_size = summarize(results, key=lambda r: r.roster_size)

    assert list(by_size) == [10, 500]
    small = by_size[10]
    assert (small.n, small.errors) == (3, 1)
    assert small.accuracy == pytest.approx(2 / 3)
    assert small.outcomes["duplicate"] == 1 and small.near_misses == 1
    assert small.mean_prompt_tokens == 100
    assert by_size[500].accuracy == 0.0


# --- harness ------------------------------------------------------------------

def test_history_renders_exactly_like_the_real_conversation_log(tmp_path, monkeypatch):
    import server.services.conversation.log as log_module

    monkeypatch.setattr(log_module, "now_in_user_timezone", lambda fmt: "2026-10-26 09:00:00")
    log = ConversationLog(tmp_path / "c.log")
    log._working_memory_log = WorkingMemoryLog(tmp_path / "wm.log")
    entries = [("user_message", "invite <alice> & bob"), ("poke_reply", "on it"), ("agent_message", "[SUCCESS] x: done")]
    log.record_user_message(entries[0][1])
    log.record_reply(entries[1][1])
    log.record_agent_message(entries[2][1])
    assert render_history(entries) == log.load_transcript()


async def test_prompt_is_built_by_the_app_with_the_case_roster(fake_llm, clean_state):
    fake_llm.for_interaction(delegate("Lunch with Alice"))
    history = [("user_message", "invite alice to lunch"), ("poke_reply", "on it")]
    await run_decision(case(history=history), ROSTER, model="m", api_key="k")

    call = fake_llm.calls[0]
    assert call.prompt.count("<agent name=") == len(ROSTER)  # the case roster, not the (empty) live one
    assert "invite alice to lunch" in call.prompt.split("</conversation_history>")[0]
    assert call.prompt.rstrip().endswith("<new_user_message>\ndid alice reply?\n</new_user_message>")
    assert "send_message_to_agent" in call.tool_names and call.system.startswith("You are OpenPoke")


async def test_routes_are_read_from_the_first_delegation(fake_llm):
    fake_llm.for_interaction(delegate("Lunch with Alice"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert (decision.routes, decision.steps) == (["Lunch with Alice"], 1)


async def test_acknowledging_first_then_delegating_is_followed(fake_llm):
    fake_llm.for_interaction(calls(("send_message_to_user", {"message": "on it"})), delegate("Lunch with Alice"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert (decision.routes, decision.steps) == (["Lunch with Alice"], 2)
    assert '"status": "delivered"' in fake_llm.calls[1].messages[-1]["content"]


async def test_raw_model_messages_are_kept_verbatim(fake_llm):
    first = calls(("send_message_to_user", {"message": "on it"}), text="thinking out loud")
    fake_llm.for_interaction(first, delegate("Lunch with Alice"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert len(decision.raw_responses) == 2
    assert decision.raw_responses[0] == first["choices"][0]["message"]


async def test_parallel_delegation_records_every_route(fake_llm):
    fake_llm.for_interaction(delegate("Lunch with Alice", "Flight to Denver"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert decision.routes == ["Lunch with Alice", "Flight to Denver"]


async def test_plain_reply_means_no_delegation(fake_llm):
    fake_llm.for_interaction(reply("you're welcome!"))
    decision = await run_decision(case("thanks!", NONE), ROSTER, model="m", api_key="k")
    assert (decision.routes, decision.hit_step_limit) == ([], False)


async def test_step_limit_is_recorded(fake_llm):
    fake_llm.for_interaction(*[calls(("wait", {"reason": "hmm"}))] * 2)
    decision = await run_decision(case(), ROSTER, model="m", api_key="k", max_steps=2)
    assert (decision.routes, decision.hit_step_limit, decision.steps) == ([], True, 2)


async def test_api_failure_is_reported_not_scored(fake_llm):
    fake_llm.for_interaction(OpenRouterError("503"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert "503" in decision.error


async def test_usage_is_summed_across_steps(fake_llm):
    first = calls(("send_message_to_user", {"message": "on it"}))
    first["usage"] = {"prompt_tokens": 1000, "completion_tokens": 10, "cost": 0.0001}
    second = delegate("Lunch with Alice")
    second["usage"] = {"prompt_tokens": 1100, "completion_tokens": 20, "cost": 0.00012}
    fake_llm.for_interaction(first, second)
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert (decision.prompt_tokens, decision.completion_tokens) == (2100, 30)
    assert decision.cost == pytest.approx(0.00022)


async def test_malformed_delegation_gets_an_error_and_the_model_can_retry(fake_llm):
    fake_llm.for_interaction(calls(("send_message_to_agent", "{not json")), delegate("Lunch with Alice"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert decision.routes == ["Lunch with Alice"]
    assert '"error": "invalid json"' in fake_llm.calls[1].messages[-1]["content"]


async def test_delegation_without_an_agent_name_is_not_a_route(fake_llm):
    fake_llm.for_interaction(calls(("send_message_to_agent", {"instructions": "x"})), reply("gave up"))
    decision = await run_decision(case(), ROSTER, model="m", api_key="k")
    assert decision.routes == []
    assert "Missing required arguments" in fake_llm.calls[1].messages[-1]["content"]
