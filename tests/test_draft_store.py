"""Tests for the draft store: exact draft text and its approval lifecycle (no LLM)."""

from datetime import datetime, timedelta, timezone

import pytest

from fakes import calls, reply
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.services import get_agent_registry, get_draft_store
from server.services.execution.drafts import DraftStateError, DraftStore


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(minutes=1)
        return self.now


@pytest.fixture
def store(tmp_path):
    return DraftStore(tmp_path / "drafts.db", now=Clock())


def record(store, draft_id="d-1", agent_id="a1", **overrides):
    fields = {"recipient": "alice@example.com", "subject": "Lunch Friday?", "body": "Are you free at noon?"}
    return store.record(draft_id, agent_id=agent_id, **{**fields, **overrides})


# --- Recording ---------------------------------------------------------------------

def test_a_recorded_draft_keeps_its_exact_text_and_starts_open(store):
    body = "Hi Alice,\n\nAre you free at noon?   Two spaces & <tags> stay.\n"
    draft = record(store, body=body, cc=["bob@example.com"], thread_id="t-9")
    assert (draft.id, draft.agent_id, draft.status) == ("d-1", "a1", "open")
    assert draft.body == body and draft.cc == ("bob@example.com",) and draft.bcc == ()
    assert draft.thread_id == "t-9" and draft.created_at
    assert store.get("d-1") == draft


def test_render_shows_the_user_exactly_what_will_be_sent(store):
    draft = record(store, cc=["bob@example.com"], bcc=["me@example.com"])
    assert draft.render() == ("To: alice@example.com\nCc: bob@example.com\nBcc: me@example.com\n"
                              "Subject: Lunch Friday?\n\nAre you free at noon?")
    assert record(store, "d-2").render() == "To: alice@example.com\nSubject: Lunch Friday?\n\nAre you free at noon?"


def test_recording_the_same_id_again_replaces_the_text_and_restarts_approval(store):
    record(store)
    store.mark_shown("d-1")
    store.approve("d-1", "yes send it")
    again = record(store, body="New text")
    assert (again.body, again.status, again.shown_at, again.approved_at) == ("New text", "open", None, None)


def test_empty_ids_are_rejected_and_unknown_ids_are_none(store):
    with pytest.raises(ValueError):
        record(store, "  ")
    assert store.get("nope") is None


def test_list_drafts_filters_by_agent_and_status_newest_first(store):
    record(store, "d-1", "a1")
    record(store, "d-2", "a2")
    record(store, "d-3", "a1")
    store.mark_deleted("d-3")
    assert [d.id for d in store.list_drafts()] == ["d-3", "d-2", "d-1"]
    assert [d.id for d in store.list_drafts(agent_id="a1")] == ["d-3", "d-1"]
    assert [d.id for d in store.list_drafts(agent_id="a1", statuses=["open"])] == ["d-1"]


def test_clear_removes_everything(store):
    record(store)
    store.clear()
    assert store.list_drafts() == []


# --- The lifecycle -------------------------------------------------------------------

def test_happy_path_open_shown_approved_sent(store):
    record(store)
    shown = store.mark_shown("d-1")
    approved = store.approve("d-1", "  Yes, send it  ")
    sent = store.mark_sent("d-1")
    assert (shown.status, approved.status, sent.status) == ("shown", "approved", "sent")
    assert shown.shown_at < approved.approved_at < sent.sent_at
    assert approved.approval_text == "Yes, send it"


def test_showing_again_keeps_the_first_shown_time_and_any_approval(store):
    record(store)
    first = store.mark_shown("d-1").shown_at
    store.approve("d-1", "ok")
    again = store.mark_shown("d-1")
    assert (again.status, again.shown_at) == ("shown", first)


@pytest.mark.parametrize("move, args", [("approve", ("ok",)), ("mark_sent", ())])
def test_an_unseen_draft_cannot_be_approved_or_sent(store, move, args):
    record(store)
    with pytest.raises(DraftStateError, match="is open"):
        getattr(store, move)("d-1", *args)
    assert store.get("d-1").status == "open"


def test_a_shown_but_unapproved_draft_cannot_be_sent(store):
    record(store)
    store.mark_shown("d-1")
    with pytest.raises(DraftStateError, match="can only become sent from: approved"):
        store.mark_sent("d-1")


@pytest.mark.parametrize("move, args", [("mark_shown", ()), ("approve", ("ok",)), ("mark_sent", ()), ("mark_deleted", ())])
def test_a_sent_draft_is_final(store, move, args):
    record(store)
    store.mark_shown("d-1")
    store.approve("d-1", "ok")
    store.mark_sent("d-1")
    with pytest.raises(DraftStateError, match="is sent"):
        getattr(store, move)("d-1", *args)


def test_a_deleted_draft_cannot_come_back(store):
    record(store)
    store.mark_deleted("d-1")
    with pytest.raises(DraftStateError, match="is deleted"):
        store.mark_shown("d-1")


def test_moves_on_unknown_drafts_say_so(store):
    with pytest.raises(DraftStateError, match="No draft 'ghost' on record"):
        store.mark_shown("ghost")


# --- Recorded by the runtime --------------------------------------------------------



@pytest.mark.usefixtures("clean_state")
async def test_worker_drafts_are_recorded_with_their_exact_text(fake_llm, fake_gmail):
    agent = get_agent_registry().create("Lunch with Alice")
    args = {"recipient_email": "alice@example.com", "subject": "Lunch?", "body": "Noon at Rosa's?\n\nBen",
            "cc": ["bob@example.com"]}
    fake_llm.for_execution(calls(("gmail_create_draft", args)), reply("Drafted"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    (draft,) = get_draft_store().list_drafts()
    assert (draft.id, draft.agent_id, draft.status) == ("r-1", agent.id, "open")
    assert (draft.recipient, draft.subject, draft.body, draft.cc) == (
        "alice@example.com", "Lunch?", "Noon at Rosa's?\n\nBen", ("bob@example.com",))


@pytest.mark.usefixtures("clean_state")
async def test_deleting_a_draft_in_gmail_marks_it_deleted(fake_llm, fake_gmail):
    agent = get_agent_registry().create("Lunch with Alice")
    fake_llm.for_execution(
        calls(("gmail_create_draft", {"recipient_email": "a@x.com", "subject": "s", "body": "b"})),
        calls(("gmail_delete_draft", {"draft_id": "r-1"})),
        reply("Done"),
    )
    await ExecutionAgentRuntime(agent.id).execute("Draft then delete")
    assert get_draft_store().get("r-1").status == "deleted"


@pytest.mark.usefixtures("clean_state")
async def test_failed_or_id_less_drafts_are_not_recorded(fake_llm, fake_gmail, monkeypatch):
    agent = get_agent_registry().create("Lunch with Alice")
    monkeypatch.setattr(fake_gmail, "_handle_gmail_create_email_draft", lambda args: {"successful": True, "data": {}})
    fake_llm.for_execution(calls(("gmail_create_draft", {"recipient_email": "a@x.com", "subject": "s", "body": "b"})),
                           calls(("gmail_delete_draft", {"draft_id": "unknown"})), reply("Hmm"))
    await ExecutionAgentRuntime(agent.id).execute("Draft it")
    assert get_draft_store().list_drafts() == []


def test_stores_from_before_forwards_are_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "drafts.db"
    with sqlite3.connect(path) as conn:  # the original table, without kind/message_id
        conn.execute("""CREATE TABLE drafts (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, recipient TEXT NOT NULL,
                        cc TEXT NOT NULL DEFAULT '[]', bcc TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL,
                        body TEXT NOT NULL, thread_id TEXT, status TEXT NOT NULL DEFAULT 'open',
                        created_at TEXT NOT NULL, shown_at TEXT, approved_at TEXT, approval_text TEXT, sent_at TEXT)""")
        conn.execute("INSERT INTO drafts (id, agent_id, recipient, subject, body, created_at) "
                     "VALUES ('r-1', 'a1', 'a@x.com', 's', 'b', '2026-10-27T09:00:00+00:00')")
    store = DraftStore(path)
    assert (store.get("r-1").kind, store.get("r-1").message_id) == ("email", None)
    assert store.record_forward(agent_id="a1", message_id="m-1", recipient="b@x.com").id == "fwd-1"


def test_forward_ids_count_up_and_render_without_a_note(store):
    first = store.record_forward(agent_id="a1", message_id="m-1", recipient="b@x.com")
    record(store, "r-1")
    second = store.record_forward(agent_id="a2", message_id="m-2", recipient="c@x.com", note="See below")
    assert (first.id, second.id) == ("fwd-1", "fwd-2")
    assert first.render() == "Forward of email m-1\nTo: b@x.com\n\n(no note)"
    assert (second.kind, second.status, second.subject) == ("forward", "open", "")


@pytest.mark.usefixtures("clean_state")
async def test_a_worker_can_discard_a_forward_it_prepared(fake_llm, fake_gmail):
    agent = get_agent_registry().create("Forward to Jake")
    fake_llm.for_execution(
        calls(("gmail_forward_email", {"message_id": "m-1", "recipient_email": "jake@law.com"})),
        calls(("gmail_delete_draft", {"draft_id": "fwd-1"})),
        reply("Discarded"),
    )
    await ExecutionAgentRuntime(agent.id).execute("Forward it, actually no")
    assert get_draft_store().get("fwd-1").status == "deleted"
    assert get_agent_registry().facts(agent.id)["draft"] == []
    assert fake_gmail.actions == []
