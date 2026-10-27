"""Tests for email urgency (P7): the classifier's verdict, and the watcher routing it to a lane."""

from datetime import datetime, timezone

import pytest

from fakes import calls, reply
from server.openrouter_client import OpenRouterError
from server.services.gmail import importance_watcher as watcher_module
from server.services.gmail.importance_classifier import EmailVerdict, classify_email_importance
from server.services.gmail.importance_watcher import ImportantEmailWatcher
from server.services.gmail.processing import ProcessedEmail
from server.services.gmail.seen_store import GmailSeenStore

EMAIL = ProcessedEmail(
    id="m-1", thread_id="t-1", query="label:INBOX", subject="Interview moved to 3pm today",
    sender="Recruiter <r@acme.com>", recipient="me@example.com",
    timestamp=datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc), label_ids=["INBOX"],
    clean_text="Hi, can we move today's interview to 3pm?", has_attachments=False,
    attachment_count=0, attachment_filenames=[],
)


def is_classifier(call):
    return "mark_email_importance" in call.tool_names


def verdict(**arguments):
    return calls(("mark_email_importance", arguments))


# --- The classifier -------------------------------------------------------------------

@pytest.mark.parametrize("urgent, expected", [(True, True), (False, False), (None, False)])
async def test_important_emails_carry_their_urgency(fake_llm, urgent, expected):
    arguments = {"important": True, "summary": "  Interview moved to 3pm today.  "}
    if urgent is not None:
        arguments["urgent"] = urgent
    fake_llm.script(verdict(**arguments), when=is_classifier)
    assert await classify_email_importance(EMAIL) == EmailVerdict("Interview moved to 3pm today.", expected)


async def test_the_model_is_told_what_urgent_means(fake_llm):
    fake_llm.script(verdict(important=False), when=is_classifier)
    await classify_email_importance(EMAIL)
    call = fake_llm.calls_to(is_classifier)[0]
    assert "Reserve urgent for what can't wait" in call.system
    urgent = call.tools[0]["function"]["parameters"]["properties"]["urgent"]
    assert urgent["type"] == "boolean" and "needs action or a reply today" in urgent["description"]


@pytest.mark.parametrize("response", [
    verdict(important=False),
    verdict(important=True),  # important without a summary
    verdict(important=True, summary="   "),
    verdict(important=True, summary="x", urgent="yes"),  # not a real boolean: never counts as urgent
    reply("no tool call"),
    calls(("mark_email_importance", "{not json")),
])
async def test_unclear_answers_never_interrupt_the_user(fake_llm, response):
    fake_llm.script(response, when=is_classifier)
    result = await classify_email_importance(EMAIL)
    assert result is None or result.urgent is False


async def test_classifier_outage_surfaces_nothing(fake_llm):
    fake_llm.script(OpenRouterError("503"), when=is_classifier)
    assert await classify_email_importance(EMAIL) is None


# --- The watcher's routing ---------------------------------------------------------------

class RecordingCenter:
    def __init__(self):
        self.sent = []

    async def notify(self, text, *, lane, source):
        self.sent.append((lane, source, text))


@pytest.fixture
def center(monkeypatch):
    fake = RecordingCenter()
    monkeypatch.setattr(watcher_module, "get_notification_center", lambda: fake)
    return fake


@pytest.fixture
def watcher(tmp_path):
    return ImportantEmailWatcher(seen_store=GmailSeenStore(tmp_path / "seen.json", 300))


@pytest.mark.parametrize("urgent, lane", [(True, "now"), (False, "digest")])
async def test_urgent_emails_go_now_and_others_to_the_digest(watcher, center, urgent, lane):
    await watcher._dispatch_summary(EMAIL, EmailVerdict("Interview moved to 3pm today.", urgent))
    assert center.sent == [(lane, "email m-1", "Important email watcher notification:\nInterview moved to 3pm today.")]
