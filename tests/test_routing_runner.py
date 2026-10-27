"""Tests for the routing eval runner and report, with a fake model (no API cost)."""

import json
import re

import pytest

from evals.routing import harness as harness_module
from evals.routing.dataset import load_cases
from evals.routing.report import write_report
from evals.routing.run import RunConfig, RunMismatchError, read_decisions, run_eval, select_smoke_cases
from fakes import calls, reply
from server.openrouter_client import OpenRouterError

CASES = {c.id: c for c in load_cases()}
TWO_CASES = [CASES["explicit-vercel-offer"], CASES["none-thanks"]]
CONFIG = RunConfig(label="t", model="fake/model", sizes=(10, 50))


class Responder:
    """Fake model: delegates to 'Vercel Job Offer' when it's offered, otherwise just replies."""

    def __init__(self, fail_message=None):
        self.calls = 0
        self.fail_message = fail_message

    async def __call__(self, *, model, messages, system=None, api_key=None, tools=None, base_url=None):
        self.calls += 1
        prompt = messages[0]["content"]
        if self.fail_message and self.fail_message in prompt:
            raise OpenRouterError("503 upstream")
        vercel_id = re.search(r'<agent id="(a\d+)" name="Vercel Job Offer"', prompt)
        response = (calls(("send_message_to_agent", {"agent_id": vercel_id.group(1), "instructions": "check"}))
                    if "vercel offer" in prompt else reply("you're welcome!"))
        response["usage"] = {"prompt_tokens": prompt.count("<agent id=") * 10 + 4000, "completion_tokens": 20, "cost": 0.0005}
        return response


@pytest.fixture
def responder(monkeypatch):
    fake = Responder()
    monkeypatch.setattr(harness_module, "request_chat_completion", fake)
    return fake


def run(cases, out_dir, config=CONFIG):
    import asyncio
    return asyncio.run(run_eval(config, cases, out_dir, api_key="k", progress=False))


def lines(out_dir):
    return (out_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()


# --- Logging ------------------------------------------------------------------------

def test_every_decision_is_logged_with_its_raw_output(responder, tmp_path):
    stats = run(TWO_CASES, tmp_path)
    assert stats == {"skipped": 0, "ran": 4, "errors": 0}

    records = [json.loads(line) for line in lines(tmp_path)]
    assert {(r["case_id"], r["roster_size"]) for r in records} == {
        ("explicit-vercel-offer", 10), ("explicit-vercel-offer", 50), ("none-thanks", 10), ("none-thanks", 50),
    }
    assert all(r["outcome"] == "correct" for r in records)
    vercel = next(r for r in records if r["case_id"] == "explicit-vercel-offer")
    assert vercel["routes"] == ["Vercel Job Offer"]
    assert vercel["raw_responses"][0]["tool_calls"][0]["function"]["name"] == "send_message_to_agent"
    assert vercel["expect"] == {"kind": "agent", "agents": ["Vercel Job Offer"]}


def test_meta_and_rosters_record_exactly_what_ran(responder, tmp_path):
    run(TWO_CASES, tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["identity"]["model"] == "fake/model"
    assert set(meta["identity"]) == {"model", "seed", "dataset_sha", "app_prompt_sha"}
    assert meta["case_ids"] == [c.id for c in TWO_CASES] and meta["sizes"] == [10, 50]
    rosters = json.loads((tmp_path / "rosters.json").read_text())
    assert len(rosters["explicit-vercel-offer"]["50"]) == 50
    assert "Vercel Job Offer" in rosters["explicit-vercel-offer"]["10"]


# --- Run once, resume, retry --------------------------------------------------------

def test_rerunning_skips_everything_already_logged(responder, tmp_path):
    run(TWO_CASES, tmp_path)
    calls_after_first_run = responder.calls
    stats = run(TWO_CASES, tmp_path)
    assert stats == {"skipped": 4, "ran": 0, "errors": 0}
    assert responder.calls == calls_after_first_run
    assert len(lines(tmp_path)) == 4


def test_adding_a_size_only_runs_the_new_decisions(responder, tmp_path):
    run(TWO_CASES, tmp_path)
    stats = run(TWO_CASES, tmp_path, RunConfig(label="t", model="fake/model", sizes=(10, 50, 200)))
    assert stats == {"skipped": 4, "ran": 2, "errors": 0}


def test_api_errors_are_logged_then_retried_on_the_next_run(monkeypatch, tmp_path):
    flaky = Responder(fail_message="thanks!")
    monkeypatch.setattr(harness_module, "request_chat_completion", flaky)
    assert run(TWO_CASES, tmp_path)["errors"] == 2

    monkeypatch.setattr(harness_module, "request_chat_completion", Responder())
    stats = run(TWO_CASES, tmp_path)
    assert stats == {"skipped": 2, "ran": 2, "errors": 0}
    latest = read_decisions(tmp_path / "decisions.jsonl")
    assert all(r["outcome"] == "correct" for r in latest.values())
    assert len(lines(tmp_path)) == 6  # the failed attempts stay in the log for the record


def test_refuses_to_mix_a_different_setup_into_an_existing_run(responder, tmp_path):
    run(TWO_CASES, tmp_path)
    with pytest.raises(RunMismatchError):
        run(TWO_CASES, tmp_path, RunConfig(label="t", model="other/model", sizes=(10, 50)))
    with pytest.raises(RunMismatchError):
        run(TWO_CASES, tmp_path, RunConfig(label="t", model="fake/model", sizes=(10, 50), seed=1))


def test_clean_identity_keeps_its_original_shape():
    # Adding variants must not change the identity of clean runs like the committed baseline.
    from evals.routing.run import run_identity

    assert set(run_identity(CONFIG)) == {"model", "seed", "dataset_sha", "app_prompt_sha"}


def test_messy_variant_puts_decoys_in_the_rosters_and_is_a_distinct_run(responder, tmp_path):
    messy = RunConfig(label="t", model="fake/model", sizes=(10, 50), variant="messy")
    run(TWO_CASES, tmp_path / "messy", messy)
    meta = json.loads((tmp_path / "messy" / "meta.json").read_text())
    assert meta["identity"]["variant"] == "messy" and "decoys_sha" in meta["identity"]
    roster = json.loads((tmp_path / "messy" / "rosters.json").read_text())["explicit-vercel-offer"]["10"]
    assert {"Vercel Offer Negotiation", "Vercel Offer Follow-up", "Vercel Job Offer"} <= set(roster)

    run(TWO_CASES, tmp_path / "clean")
    with pytest.raises(RunMismatchError):
        run(TWO_CASES, tmp_path / "clean", messy)


def test_read_decisions_prefers_success_over_error_and_latest_over_older(tmp_path):
    path = tmp_path / "decisions.jsonl"
    base = {"case_id": "c", "roster_size": 10, "repeat": 0}
    rows = [{**base, "outcome": "wrong_agent", "n": 1}, {**base, "outcome": "correct", "n": 2},
            {**base, "outcome": "error", "n": 3}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert read_decisions(path)[("c", 10, 0)]["n"] == 2


def test_smoke_selection_takes_two_cases_per_category():
    chosen = select_smoke_cases(load_cases())
    assert len(chosen) == 10
    assert len({c.category for c in chosen}) == 5


# --- Report ---------------------------------------------------------------------------

def test_report_separates_precision_from_reach_when_the_model_fans_out(tmp_path):
    meta = {"label": "t", "identity": {"model": "m", "seed": 1, "dataset_sha": "d", "app_prompt_sha": "p"}}
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    base = {"category": "explicit_followup", "roster_size": 50, "repeat": 0, "near_miss": False,
            "prompt_tokens": 100, "completion_tokens": 5, "cost": 0.0, "error": None,
            "expect": {"kind": "agent", "agents": ["Real"]}}
    rows = [
        {**base, "case_id": "exact", "routes": ["Real"], "outcome": "correct"},
        {**base, "case_id": "hedged", "routes": ["Decoy", "Real", "Other"], "outcome": "wrong_agent"},
        {**base, "case_id": "missed", "routes": ["Decoy"], "outcome": "wrong_agent"},
        {**base, "case_id": "new", "routes": ["new:x"], "outcome": "correct", "expect": {"kind": "new", "agents": []}},
    ]
    (tmp_path / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    text = write_report(tmp_path).read_text(encoding="utf-8")
    # 3 cases with a right agent: 1 exact, 2 reached it, 5 agents messaged across 3 decisions.
    assert "| 50 | 33% | 67% | 1.67 |" in text


def test_report_is_rebuilt_from_the_log_alone(responder, tmp_path):
    run([CASES["explicit-vercel-offer"], CASES["explicit-dentist-when"], CASES["none-thanks"]], tmp_path)
    calls_before = responder.calls
    text = write_report(tmp_path).read_text(encoding="utf-8")
    assert responder.calls == calls_before  # no API calls

    assert "# Routing eval: `t`" in text and "`fake/model`" in text
    # The dentist case gets a reply instead of a delegation, so 2 of 3 are correct at each size.
    assert re.search(r"\| 10 \| \*\*67%\*\* \|", text)
    assert re.search(r"\| 50 \| \*\*67%\*\* \|", text)
    assert "## Every miss (2)" in text
    assert "| 10 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |" in text
    assert "| explicit followup | 50% | 50% |" in text
