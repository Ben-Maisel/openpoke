"""Tests for the agent lifecycle (P3): idle agents with nothing open are archived on read, and revive on use."""

from datetime import datetime, timedelta, timezone

import pytest

from server.agents.interaction_agent import tools as tools_module
from server.agents.interaction_agent.agent import build_working_set, render_working_set
from server.services import get_agent_registry
from server.services.execution import lifecycle
from server.services.execution.lifecycle import is_archived, split_archived
from server.services.execution.registry import AgentRecord

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def agent(agent_id="a1", days_idle=0.0, last_used=None):
    used = last_used or (NOW - timedelta(days=days_idle)).isoformat().replace("+00:00", "Z")
    return AgentRecord(id=agent_id, name=f"Agent {agent_id}", summary="", status="active",
                       created_at=used, last_used_at=used, run_count=1)


# --- The rule ---------------------------------------------------------------------

@pytest.mark.parametrize("days_idle, archived", [(0, False), (29.9, False), (30, True), (365, True)])
def test_idle_agents_with_nothing_open_are_archived_after_the_threshold(days_idle, archived):
    assert is_archived(agent(days_idle=days_idle), {}, now=NOW, after_days=30) is archived


@pytest.mark.parametrize("facts", [{"draft": ["r-1"]}, {"trigger": ["4"]}, {"draft": ["fwd-2"], "email": ["a@x.com"]}])
def test_open_drafts_or_live_reminders_keep_an_agent_active_however_old(facts):
    assert is_archived(agent(days_idle=400), facts, now=NOW, after_days=30) is False


def test_other_facts_do_not_keep_an_agent_active():
    facts = {"email": ["a@x.com"], "person": ["Alice"], "thread": ["t-1"], "draft": [], "trigger": []}
    assert is_archived(agent(days_idle=60), facts, now=NOW, after_days=30) is True


def test_zero_turns_archiving_off():
    assert is_archived(agent(days_idle=4000), {}, now=NOW, after_days=0) is False


@pytest.mark.parametrize("last_used", ["", "not a date", "2026-09-01T12:00:00"])
def test_odd_timestamps_never_crash(last_used):
    result = is_archived(agent(last_used=last_used), {}, now=NOW, after_days=30)
    assert result is (last_used == "2026-09-01T12:00:00")  # a timestamp without a zone is read as UTC


def test_split_keeps_order_and_reads_the_threshold_from_settings(monkeypatch):
    agents = [agent("a1", 1), agent("a2", 45), agent("a3", 2), agent("a4", 90)]
    active, archived = split_archived(agents, {"a4": {"trigger": ["9"]}}, now=NOW, after_days=30)
    assert ([a.id for a in active], [a.id for a in archived]) == (["a1", "a3", "a4"], ["a2"])

    class Settings:
        agent_archive_after_days = 60

    monkeypatch.setattr(lifecycle, "get_settings", lambda: Settings())
    active, archived = split_archived(agents, {}, now=NOW)
    assert [a.id for a in archived] == ["a4"]


# --- In the app ---------------------------------------------------------------------

@pytest.fixture
def old_and_new(clean_state, monkeypatch):
    """Two agents about Alice: one last used 90 days ago, one today."""
    registry = get_agent_registry()
    monkeypatch.setattr(registry, "_now", lambda: datetime.now(timezone.utc) - timedelta(days=90))
    stale = registry.create("Lunch with Alice", summary="March lunch, long done")
    monkeypatch.undo()
    fresh = registry.create("Alice Wong Lunch", summary="Planning lunch with Alice for Friday")
    return stale, fresh


def test_archived_agents_leave_the_working_set_with_a_count(old_and_new):
    stale, fresh = old_and_new
    working_set, facts = build_working_set("lunch with alice", "")
    assert [a.id for a in working_set.agents] == [fresh.id]
    assert (working_set.total_agents, working_set.archived) == (1, 1)
    rendered = render_working_set(working_set, facts)
    assert '<archived_agents count="1">Idle for a while with nothing open, so not shown.' in rendered
    assert stale.id not in rendered


def test_when_every_agent_is_archived_the_prompt_still_says_so(clean_state, monkeypatch):
    registry = get_agent_registry()
    monkeypatch.setattr(registry, "_now", lambda: datetime.now(timezone.utc) - timedelta(days=90))
    registry.create("Old task")
    working_set, facts = build_working_set("anything", "")
    assert working_set.agents == [] and working_set.archived == 1
    assert render_working_set(working_set, facts).startswith('<archived_agents count="1">')


def test_a_live_reminder_keeps_an_old_agent_in_view(old_and_new):
    stale, _ = old_and_new
    get_agent_registry().add_facts(stale.id, "trigger", ["12"])
    working_set, _ = build_working_set("lunch with alice", "")
    assert stale.id in [a.id for a in working_set.agents] and working_set.archived == 0


def test_using_an_archived_agent_revives_it(old_and_new):
    stale, _ = old_and_new
    get_agent_registry().touch(stale.id)  # what every run does first
    working_set, _ = build_working_set("lunch with alice", "")
    assert stale.id in [a.id for a in working_set.agents] and working_set.archived == 0


class Dispatcher:
    def __init__(self):
        self.dispatched = []

    async def execute_agent(self, agent_id, instructions):
        self.dispatched.append((agent_id, instructions))


@pytest.fixture
def dispatcher(monkeypatch):
    fake = Dispatcher()
    monkeypatch.setattr(tools_module, "_EXECUTION_BATCH_MANAGER", fake)
    return fake


async def test_archived_agents_are_not_duplicates(old_and_new, dispatcher):
    stale, fresh = old_and_new
    get_agent_registry().delete(fresh.id)
    # Only the 90-day-old "Lunch with Alice" exists, so a new agent is created without a duplicate warning.
    result = tools_module.create_agent("Alice lunch", "Plan a new lunch with Alice")
    assert result.success and result.payload["new_agent_created"] is True
    assert result.payload["agent_id"] != stale.id


async def test_active_look_alikes_still_get_the_duplicate_warning(old_and_new, dispatcher):
    _, fresh = old_and_new
    result = tools_module.create_agent("Lunch with Alice Wong", "Plan lunch")
    assert result.payload["status"] == "possible_duplicate"
    assert [a["agent_id"] for a in result.payload["similar_agents"]] == [fresh.id]  # not the archived one


def test_archived_agents_are_still_found_by_search_and_marked(old_and_new):
    stale, fresh = old_and_new
    found = {a["agent_id"]: a for a in tools_module.search_agents("lunch alice").payload["agents"]}
    assert found[stale.id]["archived"] is True
    assert "archived" not in found[fresh.id]


async def test_an_archived_agent_can_still_be_messaged_by_id(old_and_new, dispatcher):
    stale, _ = old_and_new
    result = tools_module.send_message_to_agent(stale.id, "what happened in March?")
    assert result.success and result.payload["agent_id"] == stale.id
