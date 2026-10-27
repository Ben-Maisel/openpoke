"""Tests for the one-time import of a pre-registry install (P1e)."""

import json

import pytest
from fastapi.testclient import TestClient

from server.config import DATA_DIR
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.migration import migrate_legacy_roster
from server.services.execution.registry import AgentRegistry
from server.services.triggers.service import TriggerService
from server.services.triggers.store import TriggerStore


@pytest.fixture
def legacy(tmp_path):
    """A data folder as an install from before P1 left it."""
    agents_dir = tmp_path / "execution_agents"
    logs = ExecutionAgentLogStore(agents_dir)
    logs.record_request("Lunch with Alice", "Invite Alice to lunch")
    logs.record_request("Lunch with Alice", "Send draft r-1")
    logs.record_agent_response("Lunch with Alice", "Sent")
    logs.record_request("Email to Alice", "Ask Alice about Q3")  # also where "email to alice!" wrote
    (agents_dir / "roster.json").write_text(json.dumps(["Lunch with Alice", "Email to Alice", "email to alice!", "Lunch with Alice"]))
    triggers = TriggerService(TriggerStore(tmp_path / "triggers.db"))
    triggers.create_trigger(agent_id="Lunch with Alice", payload="Follow up with Alice", start_time="2030-01-01T09:00:00Z")
    return {"dir": agents_dir, "logs": logs, "triggers": triggers, "registry": AgentRegistry(tmp_path / "registry.db")}


def run(legacy):
    return migrate_legacy_roster(legacy["dir"], legacy["registry"], legacy["logs"], legacy["triggers"])


def test_each_legacy_name_becomes_one_registered_agent(legacy):
    mapping = run(legacy)
    assert list(mapping) == ["Lunch with Alice", "Email to Alice", "email to alice!"]  # duplicates collapsed
    assert len(set(mapping.values())) == 3
    assert [a.name for a in legacy["registry"].list_agents()] == ["email to alice!", "Email to Alice", "Lunch with Alice"]


def test_card_starts_from_the_agents_last_request(legacy):
    mapping = run(legacy)
    assert legacy["registry"].get(mapping["Lunch with Alice"]).summary == "Send draft r-1"


def test_memory_moves_to_the_new_id(legacy):
    mapping = run(legacy)
    history = [p for _, _, p in legacy["logs"].iter_entries(mapping["Lunch with Alice"])]
    assert history == ["Invite Alice to lunch", "Send draft r-1", "Sent"]


def test_names_that_shared_a_file_each_keep_that_history(legacy):
    mapping = run(legacy)
    for name in ("Email to Alice", "email to alice!"):
        assert [p for _, _, p in legacy["logs"].iter_entries(mapping[name])] == ["Ask Alice about Q3"]


def test_triggers_follow_their_agent_to_its_new_id(legacy):
    mapping = run(legacy)
    assert legacy["triggers"].list_triggers(agent_id="Lunch with Alice") == []
    (trigger,) = legacy["triggers"].list_triggers(agent_id=mapping["Lunch with Alice"])
    assert trigger.payload == "Follow up with Alice"


def test_import_runs_only_once(legacy):
    run(legacy)
    assert (legacy["dir"] / "roster.json.migrated").exists()
    assert (legacy["dir"] / "lunch-with-alice.log.migrated").exists()
    assert run(legacy) == {}
    assert len(legacy["registry"].list_agents()) == 3


def test_nothing_to_import_is_a_no_op(tmp_path):
    registry = AgentRegistry(tmp_path / "registry.db")
    assert migrate_legacy_roster(tmp_path, registry, ExecutionAgentLogStore(tmp_path),
                                 TriggerService(TriggerStore(tmp_path / "t.db"))) == {}


def test_unreadable_roster_is_skipped_and_left_in_place(legacy):
    (legacy["dir"] / "roster.json").write_text("{not json")
    assert run(legacy) == {}
    assert (legacy["dir"] / "roster.json").exists()


def test_app_startup_imports_a_legacy_roster(clean_state):
    from server.app import app
    from server.services import get_agent_registry

    agents_dir = DATA_DIR / "execution_agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "roster.json").write_text(json.dumps(["Dentist Reminder"]))
    try:
        with TestClient(app):
            pass
        assert [a.name for a in get_agent_registry().list_agents()] == ["Dentist Reminder"]
    finally:
        for leftover in ("roster.json", "roster.json.migrated"):
            (agents_dir / leftover).unlink(missing_ok=True)
