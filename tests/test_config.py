"""Tests for model selection settings (P26 / P15)."""

import pytest

from server.config import DEFAULT_MODEL, Settings

SLOTS = {
    "interaction_agent_model": "INTERACTION_AGENT",
    "execution_agent_model": "EXECUTION_AGENT",
    "execution_agent_search_model": "SEARCH",
    "summarizer_model": "SUMMARIZER",
    "email_classifier_model": "EMAIL_CLASSIFIER",
}


@pytest.fixture(autouse=True)
def no_model_env(monkeypatch):
    """Start every test from a clean slate, whatever a developer's .env sets."""
    monkeypatch.delenv("OPENPOKE_MODEL", raising=False)
    for slot in SLOTS.values():
        monkeypatch.delenv(f"OPENPOKE_{slot}_MODEL", raising=False)


def test_default_model_is_not_the_retired_one():
    assert DEFAULT_MODEL == "anthropic/claude-haiku-5.5"
    assert DEFAULT_MODEL != "anthropic/claude-sonnet-4"


def test_every_slot_uses_the_default_when_unset():
    settings = Settings()
    assert {field: getattr(settings, field) for field in SLOTS} == {field: DEFAULT_MODEL for field in SLOTS}


def test_openpoke_model_sets_every_slot(monkeypatch):
    monkeypatch.setenv("OPENPOKE_MODEL", "anthropic/claude-sonnet-5.5")
    settings = Settings()
    assert {getattr(settings, field) for field in SLOTS} == {"anthropic/claude-sonnet-5.5"}


@pytest.mark.parametrize("field, slot", SLOTS.items())
def test_slot_variable_overrides_only_its_slot(monkeypatch, field, slot):
    monkeypatch.setenv("OPENPOKE_MODEL", "shared/model")
    monkeypatch.setenv(f"OPENPOKE_{slot}_MODEL", "special/model")
    settings = Settings()
    assert getattr(settings, field) == "special/model"
    assert [getattr(settings, other) for other in SLOTS if other != field] == ["shared/model"] * (len(SLOTS) - 1)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_values_fall_through_to_the_default(monkeypatch, blank):
    monkeypatch.setenv("OPENPOKE_MODEL", blank)
    monkeypatch.setenv("OPENPOKE_INTERACTION_AGENT_MODEL", blank)
    assert Settings().interaction_agent_model == DEFAULT_MODEL


def test_explicit_constructor_values_still_win(monkeypatch):
    monkeypatch.setenv("OPENPOKE_MODEL", "from/env")
    assert Settings(summarizer_model="from/code").summarizer_model == "from/code"
