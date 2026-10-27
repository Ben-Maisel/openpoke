"""Tests for the memory eval's harness, runner and report, with fake models (no API cost)."""

import asyncio
import json
import re

import pytest

from evals.memory import harness as harness_module
from evals.memory.dataset import agent_name, build_journal, load_questions
from evals.memory.harness import ask, build_card, legacy_system_prompt, prepare_worker, run_times
from evals.memory.report import write_report
from evals.memory.run import RunConfig, RunMismatchError, read_answers, run_eval
from evals.memory.scoring import CORRECT, ERROR, WRONG, classify, is_correct
from fakes import calls, reply
from server.openrouter_client import OpenRouterError
from server.services import get_agent_registry, get_execution_agent_logs

pytestmark = pytest.mark.usefixtures("clean_state")

QUESTIONS = {q.id: q for q in load_questions()}
BY_TEXT = {q.question: q for q in QUESTIONS.values()}
NAME = agent_name()
JOURNAL = build_journal(20)


def _seen(text, question):
    return all(any(alt.lower() in text.lower() for alt in group) for group in question.expect)


class IdealReader:
    """Fake worker model: answers only when the answer is literally in front of it.

    With recall on offer it searches first, then fetches each ref the search turned up.
    Card refreshes get a fixed, uninformative card, so only the journal and recall can help.
    """

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    async def __call__(self, *, model, messages, system=None, api_key=None, tools=None, base_url=None):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if "status card" in (system or ""):
            return reply("Planning the Denver offsite trip.")
        question = BY_TEXT[messages[0]["content"]]
        if self.fail_on == question.id:
            raise OpenRouterError("503 upstream")
        tool_results = [m["content"] for m in messages if m["role"] == "tool"]
        usage = {"usage": {"prompt_tokens": len(system) // 4, "completion_tokens": 10, "cost": 0.001}}
        if _seen(system + "\n".join(tool_results), question):
            return {**reply(" and ".join(group[0] for group in question.expect)), **usage}
        offered = {t["function"]["name"] for t in tools or []}
        if "recall" in offered:
            if not tool_results:
                return {**calls(("recall", {"query": question.question})), **usage}
            asked = {m["tool_calls"][0]["function"]["arguments"] for m in messages if m.get("tool_calls")}
            for ref in re.findall(r"\[(r\d+)\]", tool_results[0]):
                arguments = json.dumps({"ref": ref})
                if arguments not in asked:
                    return {**calls(("recall", {"ref": ref})), **usage}
        return {**reply("I don't have that."), **usage}


@pytest.fixture
def reader(monkeypatch):
    fake = IdealReader()
    monkeypatch.setattr(harness_module, "request_chat_completion", fake)
    return fake


def run_ask(question_id, condition, journal=JOURNAL, card="Planning the Denver offsite trip."):
    async def go():
        with harness_module.eval_patches():
            return await ask(QUESTIONS[question_id], journal, condition, name=NAME, card=card, model="m", api_key="k")
    return asyncio.run(go())


# --- Scoring --------------------------------------------------------------------------

@pytest.mark.parametrize("answer, expect, correct", [
    ("Your code is QX7R2P.", [["QX7R2P"]], True),
    ("your code is qx7r2p", [["QX7R2P"]], True),
    ("Dinner at  7:30 PM\nat Tavernetta", [["7:30"], ["Tavernetta"]], True),
    ("Dinner at 7:30 PM", [["7:30"], ["Tavernetta"]], False),
    ("Total $1184.60", [["1,184.60", "1184.60"]], True),
    ("", [["x"]], False),
    ("anything", [], False),
])
def test_is_correct(answer, expect, correct):
    assert is_correct(answer, expect) is correct


def test_classify_separates_api_errors_from_wrong_answers():
    assert classify("QX7R2P", None, [["QX7R2P"]]) == CORRECT
    assert classify("no idea", None, [["QX7R2P"]]) == WRONG
    assert classify("", "429 rate limited", [["QX7R2P"]]) == ERROR
    assert classify("", "Reached tool iteration limit without final response", [["QX7R2P"]]) == WRONG


# --- Before: a faithful copy of the original app's prompt -------------------------------

def test_legacy_prompt_is_the_original_with_the_whole_cut_journal():
    prompt = legacy_system_prompt(NAME, JOURNAL, "What's my seat?")
    assert "Agent Name: Denver offsite trip\nPurpose: Handle tasks related to: Denver offsite trip\n" in prompt
    history = prompt.split("# Execution History", 1)[1]
    assert history.count("<agent_request") == len(JOURNAL) + 1
    assert history.rstrip().endswith("What's my seat?</agent_request>")
    assert "createTrigger succeeded | trigger_id=37" in history
    # Tool results were cut at 500 characters: the deep flight details never made it into memory.
    assert "UA 1482" in history and "QX7R2P" not in history and "14C" not in history
    # Draft bodies were cut at 200 characters of arguments: the expense total is lost.
    assert "Calling gmail_create_draft with:" in history and "1,184.60" not in history


def test_legacy_prompt_grows_with_the_journal():
    sizes = [len(legacy_system_prompt(NAME, build_journal(n), "q")) for n in (20, 60, 200)]
    assert sizes[0] < sizes[1] < sizes[2] and sizes[2] > 5 * sizes[0]


# --- After: the current app's real stores ------------------------------------------------

def test_prepared_worker_holds_the_journal_facts_card_and_pending_request():
    agent_id = prepare_worker(NAME, JOURNAL, "The card.", "What's my seat?")
    record = get_agent_registry().get(agent_id)
    assert (record.name, record.summary) == (NAME, "The card.")
    facts = get_agent_registry().facts(agent_id)
    assert facts["draft"] == ["d-417"]  # d-301 was deleted in a late run
    assert facts["trigger"] == ["37"]
    assert "priya.shah@acme.com" in facts["email"] and "dana.ortiz@acme.com" in facts["email"]
    entries = list(get_execution_agent_logs().iter_entries(agent_id))
    assert [e[1] for e in entries[:2]] == [run_times(1)[0]] * 2  # the story's timestamps, not today's
    assert entries[-1] == ("agent_request", run_times(len(JOURNAL) + 1)[-1], "What's my seat?")
    assert sum(tag == "agent_response" for tag, _, _ in entries) == len(JOURNAL)


def test_card_is_built_by_the_real_refresh_after_every_run(reader):
    async def go():
        with harness_module.eval_patches():
            return await build_card(NAME, JOURNAL)
    trail = asyncio.run(go())
    assert trail == ["Planning the Denver offsite trip."] * len(JOURNAL)
    refreshes = [c for c in reader.calls if "status card" in c["system"]]
    assert len(refreshes) == len(JOURNAL)
    assert "Latest request: Priya already answered" in refreshes[-1]["messages"][0]["content"]


def test_card_building_retries_then_gives_up(monkeypatch):
    attempts = []

    async def flaky(**kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise OpenRouterError("503")
        return reply("ok card")

    monkeypatch.setattr(harness_module, "request_chat_completion", flaky)

    async def go(runs):
        with harness_module.eval_patches():
            return await build_card(NAME, runs)
    assert asyncio.run(go(JOURNAL[:1])) == ["ok card"]

    async def broken(**kwargs):
        raise OpenRouterError("503")
    monkeypatch.setattr(harness_module, "request_chat_completion", broken)
    with pytest.raises(RuntimeError, match="kept failing at run 1"):
        asyncio.run(go(JOURNAL[:1]))


# --- Asking ---------------------------------------------------------------------------------

def test_before_offers_no_recall_and_live_tools_are_unavailable(monkeypatch):
    async def try_gmail(*, model, messages, system=None, api_key=None, tools=None, base_url=None):
        if not any(m["role"] == "tool" for m in messages):
            seen_tools.append({t["function"]["name"] for t in tools})
            return calls(("task_email_search", {"search_query": "seat"}))
        seen_results.append(json.loads(messages[-1]["content"]))
        return reply("Gmail is down.")

    seen_tools, seen_results = [], []
    monkeypatch.setattr(harness_module, "request_chat_completion", try_gmail)
    answer = run_ask("flight-seat", "before")
    assert "recall" not in seen_tools[0] and "gmail_create_draft" in seen_tools[0]
    assert seen_results == [{"tool": "task_email_search", "status": "error",
                             "arguments": {"search_query": "seat"}, "error": "Service unavailable"}]
    assert answer.tools_called == ["task_email_search"]


def test_old_cut_loses_deep_details_that_recall_recovers(reader):
    before = run_ask("flight-confirmation", "before")
    after = run_ask("flight-confirmation", "after")
    assert classify(before.text, before.error, QUESTIONS["flight-confirmation"].expect) == WRONG
    assert classify(after.text, after.error, QUESTIONS["flight-confirmation"].expect) == CORRECT
    assert after.recalls >= 2 and after.tools_called[0] == "recall"  # a search, then the full record


def test_full_draft_bodies_answer_without_recall(reader):
    before = run_ask("expense-total", "before")
    after = run_ask("expense-total", "after")
    assert (classify(before.text, None, QUESTIONS["expense-total"].expect), before.recalls) == (WRONG, 0)
    assert (classify(after.text, None, QUESTIONS["expense-total"].expect), after.recalls) == (CORRECT, 0)


def test_answer_records_usage_and_the_starting_prompt(reader):
    answer = run_ask("priya-email", "before")
    assert answer.text == "priya.shah@acme.com" and answer.steps == 1
    assert answer.first_prompt_tokens == answer.prompt_tokens == answer.system_prompt_chars // 4
    assert answer.cost == pytest.approx(0.001)


def test_api_failure_is_an_error_not_a_wrong_answer(monkeypatch):
    monkeypatch.setattr(harness_module, "request_chat_completion", IdealReader(fail_on="flight-seat"))
    answer = run_ask("flight-seat", "after")
    assert classify(answer.text, answer.error, QUESTIONS["flight-seat"].expect) == ERROR


# --- The runner and report ------------------------------------------------------------------

CONFIG = RunConfig(label="t", model="fake/model", card_model="fake/card", lengths=(20,))
SOME = [QUESTIONS[i] for i in ("flight-confirmation", "priya-email", "expense-total")]


def run(out_dir, config=CONFIG, questions=SOME):
    return asyncio.run(run_eval(config, questions, out_dir, api_key="k", progress=False))


def test_every_answer_is_logged_with_its_outcome(reader, tmp_path):
    assert run(tmp_path) == {"skipped": 0, "ran": 6, "errors": 0}
    answers = read_answers(tmp_path / "answers.jsonl")
    outcomes = {(q, c): r["outcome"] for (q, _, c, _), r in answers.items()}
    assert outcomes == {
        ("flight-confirmation", "before"): WRONG, ("flight-confirmation", "after"): CORRECT,
        ("priya-email", "before"): CORRECT, ("priya-email", "after"): CORRECT,
        ("expense-total", "before"): WRONG, ("expense-total", "after"): CORRECT,
    }
    cards = json.loads((tmp_path / "cards.json").read_text(encoding="utf-8"))
    assert len(cards["20"]) == 20
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert set(meta["identity"]) == {"model", "card_model", "seed", "dataset_sha", "app_sha", "legacy_sha",
                                     "history_token_budget"}


def test_rerunning_reuses_cards_and_answers(reader, tmp_path):
    run(tmp_path)
    calls_before = len(reader.calls)
    assert run(tmp_path) == {"skipped": 6, "ran": 0, "errors": 0}
    assert len(reader.calls) == calls_before


def test_api_errors_are_retried_on_the_next_run(monkeypatch, tmp_path):
    monkeypatch.setattr(harness_module, "request_chat_completion", IdealReader(fail_on="priya-email"))
    assert run(tmp_path)["errors"] == 2
    monkeypatch.setattr(harness_module, "request_chat_completion", IdealReader())
    assert run(tmp_path) == {"skipped": 4, "ran": 2, "errors": 0}


def test_refuses_to_mix_a_different_setup_into_an_existing_run(reader, tmp_path):
    run(tmp_path)
    with pytest.raises(RunMismatchError):
        run(tmp_path, RunConfig(label="t", model="other", card_model="fake/card", lengths=(20,)))


def test_before_only_runs_build_no_cards(reader, tmp_path):
    run(tmp_path, RunConfig(label="t", model="m", card_model="c", lengths=(20,), conditions=("before",)))
    assert not (tmp_path / "cards.json").exists()
    assert not [c for c in reader.calls if "status card" in c["system"]]


def test_report_is_rebuilt_from_the_log_alone(reader, tmp_path):
    run(tmp_path)
    calls_before = len(reader.calls)
    text = write_report(tmp_path).read_text(encoding="utf-8")
    assert len(reader.calls) == calls_before
    assert "# Memory eval: `t`" in text
    assert re.search(r"\| 20 \| \*\*33%\*\* \(\d+%–\d+%\) \| \*\*100%\*\* \(\d+%–\d+%\) \|", text)
    assert "- **20 runs:** Planning the Denver offsite trip." in text
    assert "## Every miss (2)" in text
    assert "| 20 | before | `flight-confirmation` | QX7R2P |" in text
