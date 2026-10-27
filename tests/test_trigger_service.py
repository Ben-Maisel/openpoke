"""Tests for TriggerService + TriggerStore against a real temporary SQLite file."""

from datetime import datetime, timedelta, timezone

import pytest

from server.services.triggers import service as service_module
from server.services.triggers.service import MISSED_TRIGGER_GRACE_PERIOD, TriggerService
from server.services.triggers.store import TriggerStore

UTC = timezone.utc
NY = "America/New_York"
# Monday 2026-10-26 12:00 UTC == 08:00 in New York (EDT, UTC-4).
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    """A controllable 'now' for the service; tests move it via clock['now']."""
    state = {"now": FIXED_NOW}
    monkeypatch.setattr(service_module, "utc_now", lambda: state["now"])
    return state


@pytest.fixture
def service(tmp_path, clock):
    return TriggerService(TriggerStore(tmp_path / "triggers.db"))


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# --- storage migration ----------------------------------------------------

def test_database_from_before_p1_is_migrated_without_losing_triggers(tmp_path, clock):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE triggers (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT NOT NULL, "
            "payload TEXT NOT NULL, start_time TEXT, next_trigger TEXT, recurrence_rule TEXT, timezone TEXT, "
            "status TEXT NOT NULL DEFAULT 'active', last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO triggers (agent_name, payload, next_trigger, created_at, updated_at) "
            "VALUES ('a3', 'Pay rent', '2026-10-27T09:00:00Z', 'x', 'x')"
        )
    migrated = TriggerService(TriggerStore(path))
    (record,) = migrated.list_triggers(agent_id="a3")
    assert (record.agent_id, record.payload) == ("a3", "Pay rent")
    TriggerService(TriggerStore(path))  # opening again is a no-op


# --- create ---------------------------------------------------------------

def test_create_one_time_trigger_fires_at_start_time(service):
    record = service.create_trigger(
        agent_id="dentist", payload="Remind about dentist", start_time="2026-10-27T15:00:00", timezone_name=NY
    )
    assert record.status == "active"
    assert record.timezone == NY
    assert record.recurrence_rule is None
    assert record.next_trigger == "2026-10-27T19:00:00Z"  # 15:00 EDT


def test_create_without_start_time_fires_now(service):
    record = service.create_trigger(agent_id="a", payload="p")
    assert record.next_trigger == "2026-10-26T12:00:00Z"


def test_create_daily_trigger_schedules_next_local_occurrence(service):
    record = service.create_trigger(
        agent_id="standup", payload="Standup", recurrence_rule="FREQ=DAILY",
        start_time="2026-10-26T09:00:00", timezone_name=NY,
    )
    assert record.next_trigger == "2026-10-26T13:00:00Z"  # 09:00 EDT today
    assert record.recurrence_rule.startswith("DTSTART;TZID=America/New_York")


def test_create_with_paused_status_is_not_due(service):
    service.create_trigger(agent_id="a", payload="p", status="paused")
    assert service.get_due_triggers(before=FIXED_NOW + timedelta(days=1)) == []


def test_create_with_invalid_start_time_raises_and_stores_nothing(service):
    with pytest.raises(ValueError):
        service.create_trigger(agent_id="a", payload="p", start_time="tomorrow at 9")
    assert service.list_triggers(agent_id="a") == []


def test_create_with_invalid_rrule_raises_and_stores_nothing(service):
    with pytest.raises(ValueError):
        service.create_trigger(agent_id="a", payload="p", recurrence_rule="FREQ=SOMETIMES")
    assert service.list_triggers(agent_id="a") == []


def test_create_with_unknown_timezone_falls_back_to_utc(service):
    record = service.create_trigger(
        agent_id="a", payload="p", start_time="2026-10-27T15:00:00", timezone_name="Mars/Olympus"
    )
    assert record.timezone == "UTC"
    assert record.next_trigger == "2026-10-27T15:00:00Z"


# --- recurrence across time ----------------------------------------------

def test_recurring_trigger_keeps_local_time_across_dst_end(service):
    # US DST ends Sunday 2026-11-01: 09:00 local moves from 13:00Z to 14:00Z.
    record = service.create_trigger(
        agent_id="standup", payload="Standup", recurrence_rule="FREQ=DAILY",
        start_time="2026-10-26T09:00:00", timezone_name=NY,
    )
    updated = service.schedule_next_occurrence(record, fired_at=_utc("2026-10-31T13:00:00Z"))
    assert updated.next_trigger == "2026-11-01T14:00:00Z"


def test_recurring_trigger_in_zero_offset_zone_keeps_local_time_across_dst_start(service):
    # London is UTC+0 in winter. UK DST starts Sunday 2027-03-28, after which
    # a 09:00 London reminder must fire at 08:00Z.
    record = service.create_trigger(
        agent_id="standup", payload="Standup", recurrence_rule="FREQ=DAILY",
        start_time="2026-11-02T09:00:00", timezone_name="Europe/London",
    )
    updated = service.schedule_next_occurrence(record, fired_at=_utc("2027-03-29T00:00:00Z"))
    assert updated.next_trigger == "2027-03-29T08:00:00Z"


def test_schedule_next_occurrence_completes_one_time_trigger(service):
    record = service.create_trigger(agent_id="a", payload="p")
    updated = service.schedule_next_occurrence(record, fired_at=FIXED_NOW)
    assert updated.status == "completed"
    assert updated.next_trigger is None


def test_recurrence_with_count_completes_after_last_occurrence(service):
    record = service.create_trigger(
        agent_id="a", payload="p", recurrence_rule="FREQ=DAILY;COUNT=2",
        start_time="2026-10-26T13:00:00Z",
    )
    second = service.schedule_next_occurrence(record, fired_at=_utc("2026-10-26T13:00:00Z"))
    assert second.next_trigger == "2026-10-27T13:00:00Z"
    done = service.schedule_next_occurrence(second, fired_at=_utc("2026-10-27T13:00:00Z"))
    assert done.status == "completed"
    assert done.next_trigger is None


# --- due / list / ownership ----------------------------------------------

def test_get_due_triggers_returns_only_active_and_due(service):
    due = service.create_trigger(agent_id="a", payload="due", start_time="2026-10-26T11:00:00Z")
    service.create_trigger(agent_id="a", payload="future", start_time="2026-10-27T11:00:00Z")
    service.create_trigger(agent_id="a", payload="paused", start_time="2026-10-26T11:00:00Z", status="paused")
    result = service.get_due_triggers(before=FIXED_NOW)
    assert [t.id for t in result] == [due.id]


def test_triggers_are_scoped_to_their_agent(service):
    mine = service.create_trigger(agent_id="alice-agent", payload="p")
    service.create_trigger(agent_id="bob-agent", payload="p")
    assert [t.id for t in service.list_triggers(agent_id="alice-agent")] == [mine.id]


def test_agent_cannot_update_another_agents_trigger(service):
    theirs = service.create_trigger(agent_id="bob-agent", payload="original")
    assert service.update_trigger(theirs.id, agent_id="alice-agent", payload="hijacked") is None
    assert service.list_triggers(agent_id="bob-agent")[0].payload == "original"


def test_update_missing_trigger_returns_none(service):
    assert service.update_trigger(9999, agent_id="a", payload="x") is None


# --- update / pause / resume ---------------------------------------------

def test_update_payload_keeps_schedule(service):
    record = service.create_trigger(agent_id="a", payload="old", start_time="2026-10-27T15:00:00Z")
    updated = service.update_trigger(record.id, agent_id="a", payload="new")
    assert updated.payload == "new"
    assert updated.next_trigger == record.next_trigger


def test_update_start_time_reschedules(service):
    record = service.create_trigger(agent_id="a", payload="p", start_time="2026-10-27T15:00:00Z")
    updated = service.update_trigger(record.id, agent_id="a", start_time="2026-10-28T15:00:00Z")
    assert updated.next_trigger == "2026-10-28T15:00:00Z"


def test_resume_within_grace_period_keeps_original_time(service, clock):
    record = service.create_trigger(agent_id="a", payload="p", start_time="2026-10-26T12:30:00Z")
    service.update_trigger(record.id, agent_id="a", status="paused")
    clock["now"] = _utc("2026-10-26T12:33:00Z")  # 3 min after it was due
    resumed = service.update_trigger(record.id, agent_id="a", status="active")
    assert resumed.next_trigger == "2026-10-26T12:30:00Z"


def test_resume_one_time_trigger_after_grace_period_fires_now(service, clock):
    record = service.create_trigger(agent_id="a", payload="p", start_time="2026-10-26T12:30:00Z")
    service.update_trigger(record.id, agent_id="a", status="paused")
    clock["now"] = _utc("2026-10-26T12:30:00Z") + MISSED_TRIGGER_GRACE_PERIOD + timedelta(minutes=1)
    resumed = service.update_trigger(record.id, agent_id="a", status="active")
    assert resumed.next_trigger == "2026-10-26T12:36:00Z"


def test_resume_recurring_trigger_after_grace_period_skips_missed_runs(service, clock):
    record = service.create_trigger(
        agent_id="a", payload="p", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T13:00:00Z"
    )
    service.update_trigger(record.id, agent_id="a", status="paused")
    clock["now"] = _utc("2026-10-29T18:00:00Z")  # several runs missed
    resumed = service.update_trigger(record.id, agent_id="a", status="active")
    assert resumed.next_trigger == "2026-10-30T13:00:00Z"


@pytest.mark.parametrize("word", ["cancelled", "canceled", "deleted", "stopped", "disabled"])
def test_unrecognised_status_is_rejected_and_changes_nothing(service, word):
    # An LLM trying to cancel a reminder with a natural word gets a clear error to retry with (P22).
    record = service.create_trigger(agent_id="a", payload="p", start_time="2026-10-27T15:00:00Z")
    with pytest.raises(ValueError, match="use 'paused' to cancel"):
        service.update_trigger(record.id, agent_id="a", status=word)
    assert service.list_triggers(agent_id="a")[0] == record


def test_cancelling_a_paused_overdue_reminder_does_not_fire_it(service, clock):
    # The P22 scenario: before the fix, "cancelled" became "active", which resumed
    # the paused reminder and, because it was overdue, made it fire immediately.
    record = service.create_trigger(agent_id="a", payload="Pay rent", start_time="2026-10-26T13:00:00Z")
    service.update_trigger(record.id, agent_id="a", status="paused")
    clock["now"] = _utc("2026-10-27T09:00:00Z")
    with pytest.raises(ValueError):
        service.update_trigger(record.id, agent_id="a", status="cancelled")
    assert service.list_triggers(agent_id="a")[0].status == "paused"
    assert service.get_due_triggers(before=clock["now"]) == []


@pytest.mark.parametrize("bad", ["disabled", "inactive"])
def test_creating_with_an_unrecognised_status_stores_nothing(service, bad):
    with pytest.raises(ValueError):
        service.create_trigger(agent_id="a", payload="p", status=bad)
    assert service.list_triggers(agent_id="a") == []


# --- notify mode (P7) ---------------------------------------------------------

def test_triggers_are_reminders_unless_told_otherwise(service):
    assert service.create_trigger(agent_id="a", payload="Call the dentist").notify == "always"
    check = service.create_trigger(agent_id="a", payload="Check for Alice's reply", notify=" On_Change ")
    assert check.notify == "on_change"


def test_unknown_notify_modes_are_rejected_with_a_clear_message(service):
    with pytest.raises(ValueError, match="Use 'always' for a reminder"):
        service.create_trigger(agent_id="a", payload="x", notify="sometimes")


def test_notify_can_be_changed_later(service):
    record = service.create_trigger(agent_id="a", payload="Check for Alice's reply")
    updated = service.update_trigger(record.id, agent_id="a", notify="on_change")
    assert updated.notify == "on_change"
    assert service.update_trigger(record.id, agent_id="a", payload="new text").notify == "on_change"


def test_databases_from_before_notify_are_migrated(tmp_path, clock):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE triggers (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
                        payload TEXT NOT NULL, start_time TEXT, next_trigger TEXT, recurrence_rule TEXT, timezone TEXT,
                        status TEXT NOT NULL DEFAULT 'active', last_error TEXT, created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO triggers (agent_id, payload, created_at, updated_at) VALUES ('a', 'old', 't', 't')")
    service = TriggerService(TriggerStore(path))
    assert [t.notify for t in service.list_triggers(agent_id="a")] == ["always"]


def test_the_worker_tool_sets_and_reports_notify(clean_state):
    from server.agents.execution_agent.tools import triggers as trigger_tools

    tools = trigger_tools.build_registry("a9")
    created = tools["createTrigger"](payload="Check for Alice's reply", notify="on_change")
    assert created["notify"] == "on_change"
    assert tools["listTriggers"]()["triggers"][0]["notify"] == "on_change"
    assert "Invalid notify" in tools["createTrigger"](payload="x", notify="sometimes")["error"]
