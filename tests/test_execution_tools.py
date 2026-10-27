"""Tests for the execution agent's tool layer: Gmail tools and trigger tools."""

import pytest

from server.agents.execution_agent.tools import get_tool_registry, get_tool_schemas
from server.agents.execution_agent.tools import gmail as gmail_tools
from server.agents.execution_agent.tools import triggers as trigger_tools
from server.services import get_execution_agent_logs

pytestmark = pytest.mark.usefixtures("clean_state")


# --- Registry ---------------------------------------------------------------

def test_every_advertised_tool_has_an_implementation_and_vice_versa():
    schema_names = {schema["function"]["name"] for schema in get_tool_schemas()}
    assert schema_names == set(get_tool_registry(agent_id="a"))


def test_tool_names_are_unique():
    names = [schema["function"]["name"] for schema in get_tool_schemas()]
    assert len(names) == len(set(names))


# --- Gmail tools ------------------------------------------------------------

@pytest.mark.parametrize(
    "tool, kwargs, composio_action",
    [
        (gmail_tools.gmail_create_draft, {"recipient_email": "a@x.com", "subject": "s", "body": "b"}, "GMAIL_CREATE_EMAIL_DRAFT"),
        (gmail_tools.gmail_execute_draft, {"draft_id": "r-1"}, "GMAIL_SEND_DRAFT"),
        (gmail_tools.gmail_delete_draft, {"draft_id": "r-1"}, "GMAIL_DELETE_DRAFT"),
        (gmail_tools.gmail_forward_email, {"message_id": "m-1", "recipient_email": "b@x.com"}, "GMAIL_FORWARD_MESSAGE"),
        (gmail_tools.gmail_reply_to_thread, {"thread_id": "t-1", "recipient_email": "a@x.com", "message_body": "hi"}, "GMAIL_REPLY_TO_THREAD"),
        (gmail_tools.gmail_get_contacts, {}, "GMAIL_GET_CONTACTS"),
        (gmail_tools.gmail_get_people, {}, "GMAIL_GET_PEOPLE"),
        (gmail_tools.gmail_list_drafts, {}, "GMAIL_LIST_DRAFTS"),
        (gmail_tools.gmail_search_people, {"query": "alice"}, "GMAIL_SEARCH_PEOPLE"),
    ],
)
def test_each_gmail_tool_calls_the_matching_composio_action(fake_gmail, tool, kwargs, composio_action):
    tool(**kwargs)
    assert fake_gmail.action_names() == [composio_action]


def test_unset_optional_arguments_are_not_sent(fake_gmail):
    gmail_tools.gmail_create_draft(recipient_email="a@x.com", subject="s", body="b")
    (_, sent_args), = fake_gmail.actions
    assert sent_args == {"recipient_email": "a@x.com", "subject": "s", "body": "b"}


def test_search_people_renames_paging_arguments(fake_gmail):
    gmail_tools.gmail_search_people(query="alice", page_size=5, page_token="p2")
    (_, sent_args), = fake_gmail.actions
    assert (sent_args["pageSize"], sent_args["pageToken"]) == (5, "p2")


def test_draft_then_send_round_trip(fake_gmail):
    created = gmail_tools.gmail_create_draft(recipient_email="a@x.com", subject="s", body="b")
    draft_id = created["data"]["id"]
    gmail_tools.gmail_execute_draft(draft_id=draft_id)
    assert [m["recipient_email"] for m in fake_gmail.sent] == ["a@x.com"]


def test_sending_an_unknown_draft_reports_failure(fake_gmail):
    result = gmail_tools.gmail_execute_draft(draft_id="r-404")
    assert result["successful"] is False
    assert fake_gmail.sent == []


def test_tools_refuse_when_gmail_is_not_connected(fake_gmail):
    fake_gmail.connected = False
    result = gmail_tools.gmail_execute_draft(draft_id="r-1")
    assert "Gmail not connected" in result["error"]
    assert fake_gmail.actions == []


def test_composio_failure_is_logged_and_re_raised(fake_gmail):
    fake_gmail.fail_with = RuntimeError("Composio timeout")
    with pytest.raises(RuntimeError, match="Composio timeout"):
        gmail_tools.gmail_list_drafts()


# --- Trigger tools ----------------------------------------------------------

def tools_for(agent):
    return trigger_tools.build_registry(agent)


def test_create_trigger_tool_returns_schedule_without_writing_its_own_journal_entry():
    result = tools_for("Dentist")["createTrigger"](payload="Remind about dentist", start_time="2030-01-01T09:00:00Z")
    assert result["status"] == "active"
    assert result["next_trigger"] == "2030-01-01T09:00:00Z"
    # The runtime records the call in the agent's journal; the tool itself must not add a second entry.
    assert list(get_execution_agent_logs().iter_entries("Dentist")) == []


def test_list_triggers_tool_only_shows_the_agents_own_triggers():
    tools_for("Dentist")["createTrigger"](payload="mine")
    tools_for("Gym")["createTrigger"](payload="theirs")
    listed = tools_for("Dentist")["listTriggers"]()["triggers"]
    assert [t["payload"] for t in listed] == ["mine"]


def test_update_trigger_tool_pauses_own_trigger():
    created = tools_for("Dentist")["createTrigger"](payload="p", start_time="2030-01-01T09:00:00Z")
    updated = tools_for("Dentist")["updateTrigger"](trigger_id=created["trigger_id"], status="paused")
    assert updated["status"] == "paused"


def test_update_trigger_tool_cannot_touch_another_agents_trigger():
    created = tools_for("Dentist")["createTrigger"](payload="p")
    result = tools_for("Gym")["updateTrigger"](trigger_id=created["trigger_id"], payload="hijacked")
    assert result == {"error": f"Trigger {created['trigger_id']} not found"}


@pytest.mark.parametrize("bad_id", ["abc", None, "1.5"])
def test_update_trigger_tool_rejects_non_integer_ids(bad_id):
    assert tools_for("Dentist")["updateTrigger"](trigger_id=bad_id) == {"error": "trigger_id must be an integer"}


def test_trigger_status_is_limited_to_valid_values_in_the_tool_definitions():
    schemas = {s["function"]["name"]: s["function"]["parameters"]["properties"] for s in trigger_tools.get_schemas()}
    assert schemas["createTrigger"]["status"]["enum"] == ["active", "paused"]
    assert schemas["updateTrigger"]["status"]["enum"] == ["active", "paused", "completed"]


def test_update_trigger_tool_returns_a_correctable_error_for_an_unknown_status():
    created = tools_for("Rent")["createTrigger"](payload="Pay rent", start_time="2030-01-01T09:00:00Z")
    result = tools_for("Rent")["updateTrigger"](trigger_id=created["trigger_id"], status="cancelled")
    assert "Invalid status 'cancelled'" in result["error"] and "use 'paused' to cancel" in result["error"]
    assert tools_for("Rent")["listTriggers"]()["triggers"][0]["status"] == "active"


def test_create_trigger_tool_returns_error_for_invalid_rule():
    result = tools_for("Dentist")["createTrigger"](payload="p", recurrence_rule="FREQ=SOMETIMES")
    assert "error" in result
    assert tools_for("Dentist")["listTriggers"]()["triggers"] == []
