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

from fakes import LLM_CALL_SITES, ScriptedLLM  # noqa: E402


@pytest.fixture
def fake_llm(monkeypatch):
    """Route every LLM call in the server to a ScriptedLLM for this test."""
    llm = ScriptedLLM()
    for module_path in LLM_CALL_SITES:
        monkeypatch.setattr(importlib.import_module(module_path), "request_chat_completion", llm)
    return llm


@pytest.fixture
def clean_state():
    """Reset the server's global stores (chat, roster, agent logs, triggers) around a test."""
    from server.services import (
        get_agent_roster,
        get_conversation_log,
        get_execution_agent_logs,
        get_trigger_service,
    )

    def reset():
        get_conversation_log().clear()
        get_agent_roster().clear()
        get_execution_agent_logs().clear_all()
        get_trigger_service().clear_all()

    reset()
    yield
    reset()
