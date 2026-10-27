"""Tests for the urgency eval runner and report, with a fake classifier model (no API cost)."""

import asyncio
import json

import pytest

from evals.notifications.dataset import load_emails
from evals.notifications.urgency import read_decisions, run_eval, write_report
from fakes import calls
from server.openrouter_client import OpenRouterError

EMAILS = load_emails()


class FakeClassifier:
    """Answers each email by its label, except the ones told to fail or to be judged wrongly."""

    def __init__(self, wrong=(), fail=()):
        self.wrong, self.fail, self.calls = set(wrong), set(fail), 0

    def respond(self):
        by_subject = {e.subject: e for e in EMAILS}

        async def answer(*, model, messages, system=None, api_key=None, tools=None, base_url=None):
            self.calls += 1
            email = next(e for s, e in by_subject.items() if f"Subject: {s}\n" in messages[0]["content"])
            if email.id in self.fail:
                raise OpenRouterError("503 upstream")
            label = email.label
            if email.id in self.wrong:
                label = {"urgent": "important", "important": "urgent", "not_important": "important"}[label]
            arguments = {"important": label != "not_important", "urgent": label == "urgent", "summary": f"About {email.id}"}
            return {**calls(("mark_email_importance", arguments)), "usage": {"prompt_tokens": 300, "cost": 0.0001}}

        return answer


@pytest.fixture
def classifier(monkeypatch):
    from server.services.gmail import importance_classifier

    fake = FakeClassifier(wrong={"u3", "n3"})
    monkeypatch.setattr(importance_classifier, "request_chat_completion", fake.respond())
    return fake


def run(out_dir, emails=EMAILS):
    return asyncio.run(run_eval("t", "fake/model", emails, 1, out_dir, progress=False))


def test_every_judgement_is_logged_with_its_lane(classifier, tmp_path):
    assert run(tmp_path) == {"ran": 24, "errors": 0, "skipped": 0}
    decisions = {k[0]: r for k, r in read_decisions(tmp_path / "decisions.jsonl").items()}
    assert (decisions["u1"]["lane"], decisions["i1"]["lane"], decisions["n1"]["lane"]) == ("now", "digest", None)
    assert (decisions["u3"]["lane"], decisions["n3"]["lane"]) == ("digest", "digest")  # the two planted mistakes
    assert decisions["u1"]["summary"] == "About u1" and decisions["u1"]["raw_response"]["choices"]


def test_rerunning_skips_what_is_logged(classifier, tmp_path):
    run(tmp_path)
    before = classifier.calls
    assert run(tmp_path) == {"ran": 0, "errors": 0, "skipped": 24}
    assert classifier.calls == before


def test_api_errors_are_recorded_not_scored_and_retried(monkeypatch, tmp_path):
    from server.services.gmail import importance_classifier

    flaky = FakeClassifier(fail={"u1"})
    monkeypatch.setattr(importance_classifier, "request_chat_completion", flaky.respond())
    assert run(tmp_path)["errors"] == 1
    assert read_decisions(tmp_path / "decisions.jsonl")[("u1", 0)]["error"] == "503 upstream"

    monkeypatch.setattr(importance_classifier, "request_chat_completion", FakeClassifier().respond())
    assert run(tmp_path) == {"ran": 1, "errors": 0, "skipped": 23}
    assert read_decisions(tmp_path / "decisions.jsonl")[("u1", 0)]["lane"] == "now"


def test_the_classifier_is_restored_after_a_run(classifier, tmp_path):
    from server.services.gmail import importance_classifier

    patched = importance_classifier.request_chat_completion
    run(tmp_path)
    assert importance_classifier.request_chat_completion is patched


def test_report_scores_missed_urgent_false_interrupts_and_drops(classifier, tmp_path):
    run(tmp_path)
    text = write_report(tmp_path).read_text(encoding="utf-8")
    assert "| Exact lane | **92%** (22/24," in text
    assert "| Missed urgent (urgent email didn't interrupt) | **12%** (1/8," in text
    assert "| False interrupts (non-urgent email interrupted) | **0%** (0/16," in text
    assert "| Dropped (important email not surfaced at all) | **0%** (0/8," in text
    assert "| urgent | 7 | 1 | 0 |" in text and "| not important | 0 | 1 | 7 |" in text
    assert "## Every mistake (2)" in text and "| u3 | urgent | now | digest |" in text
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert set(meta["identity"]) == {"model", "dataset_sha", "classifier_sha"}
