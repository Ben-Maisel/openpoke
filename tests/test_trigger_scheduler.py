"""Tests for TriggerScheduler with the execution agent replaced by a fake."""

import asyncio
from datetime import datetime, timezone

import pytest

from server.agents.execution_agent.runtime import ExecutionResult
from server.services import trigger_scheduler as scheduler_module
from server.services.triggers import service as service_module
from server.services.triggers.service import TriggerService
from server.services.triggers.store import TriggerStore

UTC = timezone.utc
FIXED_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


class FakeBatchManager:
    """Stands in for ExecutionBatchManager; records calls instead of running an LLM."""

    calls: list = []
    outcome: object = "success"  # "success", "failure", or an Exception to raise
    response: str = "done"
    gate: asyncio.Event | None = None

    async def execute_agent(self, agent_id, instructions, request_id=None, *, report=True):
        assert report is False  # trigger results go through the notification lanes, not the batch (P7)
        FakeBatchManager.calls.append((agent_id, instructions))
        if FakeBatchManager.gate is not None:
            await FakeBatchManager.gate.wait()
        if isinstance(FakeBatchManager.outcome, Exception):
            raise FakeBatchManager.outcome
        success = FakeBatchManager.outcome == "success"
        return ExecutionResult(
            agent_name=agent_id, agent_id=agent_id, success=success,
            response=FakeBatchManager.response if success else "boom", error=None if success else "boom",
        )


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "utc_now", lambda: FIXED_NOW)
    return TriggerService(TriggerStore(tmp_path / "triggers.db"))


class RecordingCenter:
    def __init__(self):
        self.sent = []

    async def notify(self, text, *, lane, source):
        self.sent.append((lane, source, text))


@pytest.fixture
def notifications():
    return RecordingCenter()


@pytest.fixture
def scheduler(service, monkeypatch, notifications):
    FakeBatchManager.calls = []
    FakeBatchManager.outcome = "success"
    FakeBatchManager.gate = None
    FakeBatchManager.response = "done"
    monkeypatch.setattr(scheduler_module, "get_notification_center", lambda: notifications)
    monkeypatch.setattr(scheduler_module, "get_execution_batch_manager", FakeBatchManager)
    monkeypatch.setattr(scheduler_module, "get_trigger_service", lambda: service)
    monkeypatch.setattr(scheduler_module, "_utc_now", lambda: FIXED_NOW)
    return scheduler_module.TriggerScheduler(poll_interval_seconds=0.01)


def _reload(service, record):
    return service.list_triggers(agent_id=record.agent_id)[0]


# --- executing a single trigger ------------------------------------------

async def test_successful_one_time_trigger_runs_owner_agent_and_completes(scheduler, service):
    record = service.create_trigger(agent_id="dentist", payload="Remind about dentist")
    await scheduler._execute_trigger(record)

    assert FakeBatchManager.calls[0][0] == "dentist"
    assert "Remind about dentist" in FakeBatchManager.calls[0][1]
    after = _reload(service, record)
    assert (after.status, after.next_trigger) == ("completed", None)


async def test_successful_recurring_trigger_is_rescheduled(scheduler, service):
    record = service.create_trigger(
        agent_id="a", payload="p", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T12:00:00Z"
    )
    await scheduler._execute_trigger(record)
    after = _reload(service, record)
    assert (after.status, after.next_trigger) == ("active", "2026-10-27T12:00:00Z")


async def test_failed_one_time_trigger_records_error_and_stops(scheduler, service):
    FakeBatchManager.outcome = "failure"
    record = service.create_trigger(agent_id="a", payload="p")
    await scheduler._execute_trigger(record)
    after = _reload(service, record)
    assert after.last_error == "boom"
    assert after.next_trigger is None


async def test_failed_recurring_trigger_keeps_error_and_is_rescheduled(scheduler, service):
    FakeBatchManager.outcome = "failure"
    record = service.create_trigger(
        agent_id="a", payload="p", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T12:00:00Z"
    )
    await scheduler._execute_trigger(record)
    after = _reload(service, record)
    assert after.next_trigger == "2026-10-27T12:00:00Z"
    assert after.last_error == "boom"


async def test_a_later_success_clears_the_previous_error(scheduler, service):
    FakeBatchManager.outcome = "failure"
    record = service.create_trigger(
        agent_id="a", payload="p", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T12:00:00Z"
    )
    await scheduler._execute_trigger(record)
    FakeBatchManager.outcome = "success"
    await scheduler._execute_trigger(_reload(service, record))
    assert _reload(service, record).last_error is None


async def test_failure_is_visible_to_the_owning_agent_through_list_triggers(scheduler, service, monkeypatch):
    from server.agents.execution_agent.tools import triggers as trigger_tools

    monkeypatch.setattr(trigger_tools, "_TRIGGER_SERVICE", service)
    FakeBatchManager.outcome = "failure"
    record = service.create_trigger(
        agent_id="Morning Summary", payload="p", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T12:00:00Z"
    )
    await scheduler._execute_trigger(record)
    listed = trigger_tools.build_registry("Morning Summary")["listTriggers"]()["triggers"]
    assert listed[0]["last_error"] == "boom"


async def test_crashing_agent_is_treated_as_failure(scheduler, service):
    FakeBatchManager.outcome = RuntimeError("agent exploded")
    record = service.create_trigger(agent_id="a", payload="p")
    await scheduler._execute_trigger(record)
    after = _reload(service, record)
    assert "agent exploded" in after.last_error
    assert after.next_trigger is None
    assert record.id not in scheduler._in_flight


def test_instructions_include_trigger_metadata_and_payload(scheduler, service):
    record = service.create_trigger(
        agent_id="a", payload="Check the flight", recurrence_rule="FREQ=DAILY", start_time="2026-10-26T12:00:00Z"
    )
    text = scheduler._format_instructions(record, FIXED_NOW)
    assert f"Trigger ID: {record.id}" in text
    assert "Recurrence:" in text
    assert text.endswith("Payload:\nCheck the flight")


# --- polling --------------------------------------------------------------

async def test_poll_dispatches_each_due_trigger_once_while_in_flight(scheduler, service):
    FakeBatchManager.gate = asyncio.Event()  # hold the run open
    service.create_trigger(agent_id="a", payload="p")

    await scheduler._poll_once()
    await scheduler._poll_once()  # still due, but already running
    await asyncio.sleep(0)
    assert len(FakeBatchManager.calls) == 1

    FakeBatchManager.gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert scheduler._in_flight == set()


async def test_poll_ignores_paused_and_future_triggers(scheduler, service):
    service.create_trigger(agent_id="a", payload="p", status="paused")
    service.create_trigger(agent_id="a", payload="p", start_time="2026-10-27T12:00:00Z")
    await scheduler._poll_once()
    await asyncio.sleep(0)
    assert FakeBatchManager.calls == []


async def test_start_and_stop_are_idempotent(scheduler):
    await scheduler.start()
    first_task = scheduler._task
    await scheduler.start()
    assert scheduler._task is first_task
    await scheduler.stop()
    await scheduler.stop()
    assert scheduler._task is None


# --- notification lanes (P7) ------------------------------------------------

async def test_a_reminder_reaches_the_user_now(scheduler, service, notifications):
    record = service.create_trigger(agent_id="a7", payload="Call the dentist")
    await scheduler._execute_trigger(record)
    assert notifications.sent == [("now", f"trigger {record.id}", "[SUCCESS] a7 (a7): done")]


async def test_a_background_check_with_news_goes_to_the_digest(scheduler, service, notifications):
    FakeBatchManager.response = "Alice replied: Friday works."
    record = service.create_trigger(agent_id="a7", payload="Check for Alice's reply", notify="on_change")
    await scheduler._execute_trigger(record)
    assert notifications.sent == [("digest", f"trigger {record.id}", "[SUCCESS] a7 (a7): Alice replied: Friday works.")]


@pytest.mark.parametrize("response", ["NO_UPDATE", "  NO_UPDATE\n(no reply from Alice yet)"])
async def test_a_background_check_with_nothing_new_stays_silent(scheduler, service, notifications, response):
    FakeBatchManager.response = response
    record = service.create_trigger(agent_id="a7", payload="Check for Alice's reply", notify="on_change")
    await scheduler._execute_trigger(record)
    assert [lane for lane, _, _ in notifications.sent] == ["silent"]


async def test_a_failed_background_check_is_not_silent(scheduler, service, notifications):
    FakeBatchManager.outcome = "failure"
    record = service.create_trigger(agent_id="a7", payload="Check for Alice's reply", notify="on_change")
    await scheduler._execute_trigger(record)
    assert notifications.sent == [("digest", f"trigger {record.id}", "[FAILED] a7 (a7): boom")]


def test_background_checks_are_told_how_to_say_nothing_changed(scheduler, service):
    check = service.create_trigger(agent_id="a", payload="Check for Alice's reply", notify="on_change")
    reminder = service.create_trigger(agent_id="a", payload="Call the dentist")
    assert scheduler._format_instructions(check, FIXED_NOW).endswith("reply with exactly NO_UPDATE.")
    assert "NO_UPDATE" not in scheduler._format_instructions(reminder, FIXED_NOW)
