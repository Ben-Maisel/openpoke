"""The tuning knobs live in one place (server/config.py) and actually drive behavior."""

from server.agents.execution_agent import memory
from server.agents.execution_agent.memory import render_facts
from server.config import Settings
from server.services.execution import matching, search
from server.services.execution.matching import find_possible_duplicates
from server.services.execution.registry import AgentRecord
from server.services.execution.search import select_working_set


def agent(index, name):
    used = f"2026-10-{index + 1:02d}T09:00:00Z"
    return AgentRecord(id=f"a{index}", name=name, summary="", status="active",
                       created_at=used, last_used_at=used, run_count=1)


def test_working_set_sizes_come_from_settings(monkeypatch):
    agents = [agent(i, f"Alice task {i}") for i in range(1, 10)]
    monkeypatch.setattr(search, "get_settings", lambda: Settings(working_set_recent=2, working_set_matches=1))
    working_set = select_working_set("alice", agents)
    assert (len(working_set.hot), len(working_set.matches)) == (2, 1)


def test_duplicate_threshold_comes_from_settings(monkeypatch):
    agents = [agent(1, "Lunch with Alice Wong")]
    assert find_possible_duplicates("Alice Wong dinner", agents)  # 2 of 3 words shared: a duplicate at 2/3
    monkeypatch.setattr(matching, "get_settings", lambda: Settings(duplicate_name_threshold=0.9))
    assert find_possible_duplicates("Alice Wong dinner", agents) == []


def test_digest_limits_come_from_settings(monkeypatch):
    monkeypatch.setattr(memory, "get_settings", lambda: Settings(digest_max_listed=2))
    assert render_facts({"email": ["a@x.com", "b@x.com", "c@x.com"]}) == "- Email addresses: a@x.com, b@x.com (+1 more)"


def test_environment_overrides_use_the_same_reader_as_the_settings(monkeypatch):
    # OPENPOKE_ARCHIVE_AFTER_DAYS and OPENPOKE_EXECUTION_HISTORY_TOKENS are read with _env_int when config loads.
    from server.config import _env_int

    monkeypatch.setenv("OPENPOKE_ARCHIVE_AFTER_DAYS", "7")
    assert _env_int("OPENPOKE_ARCHIVE_AFTER_DAYS", 30) == 7
    monkeypatch.delenv("OPENPOKE_ARCHIVE_AFTER_DAYS")
    assert _env_int("OPENPOKE_ARCHIVE_AFTER_DAYS", 30) == 30
