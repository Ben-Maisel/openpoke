"""Shared pytest setup.

This module runs before any test imports `server`, which matters because the
server's stores pick their file paths at import time. Everything below must
happen before that first import.
"""

import atexit
import os
import shutil
import tempfile

# Send all runtime data to a throwaway directory so tests never touch server/data.
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="openpoke-test-data-")
atexit.register(shutil.rmtree, _TEST_DATA_DIR, ignore_errors=True)
os.environ["OPENPOKE_DATA_DIR"] = _TEST_DATA_DIR

# Pin credentials to inert values so a developer's .env can never cause real API calls.
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
os.environ["COMPOSIO_API_KEY"] = ""
os.environ["COMPOSIO_GMAIL_AUTH_CONFIG_ID"] = ""


import importlib  # noqa: E402  (must come after the environment setup above)

import pytest  # noqa: E402

from fakes import GMAIL_CALL_SITES, LLM_CALL_SITES, FakeGmail, ScriptedLLM, is_card_refresh, reply  # noqa: E402


@pytest.fixture
def fake_llm(monkeypatch):
    """Route every LLM call in the server to a ScriptedLLM for this test.

    Card refreshes run after every agent run, so they get a canned answer unless a test scripts them.
    """
    llm = ScriptedLLM()
    llm.default(reply("Fake card summary."), when=is_card_refresh)
    for module_path in LLM_CALL_SITES:
        monkeypatch.setattr(importlib.import_module(module_path), "request_chat_completion", llm)
    return llm


@pytest.fixture
def fake_gmail(monkeypatch):
    """Route every Composio Gmail call in the server to an in-memory FakeGmail."""
    gmail = FakeGmail()
    for module_path in GMAIL_CALL_SITES:
        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, "execute_gmail_tool", gmail.execute_gmail_tool)
        monkeypatch.setattr(module, "get_active_gmail_user_id", gmail.get_active_gmail_user_id)
    return gmail


@pytest.fixture
def batch_manager(monkeypatch):
    """A fresh execution batch manager per test, so no pending batch leaks between tests."""
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager
    from server.agents.interaction_agent import tools as interaction_tools

    manager = ExecutionBatchManager(timeout_seconds=2)
    monkeypatch.setattr(interaction_tools, "_EXECUTION_BATCH_MANAGER", manager)
    return manager


@pytest.fixture
def clean_state():
    """Reset the server's global stores (chat, agent registry, agent logs, triggers) around a test.

    Agent IDs are never reused, so tests must use the IDs `create` returns rather than assume `a1`.
    """
    from server.services import (
        get_agent_registry,
        get_conversation_log,
        get_execution_agent_logs,
        get_trigger_service,
    )

    def reset():
        get_conversation_log().clear()
        get_agent_registry().clear()
        get_execution_agent_logs().clear_all()
        get_trigger_service().clear_all()

    reset()
    yield
    reset()
