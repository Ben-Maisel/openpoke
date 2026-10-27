"""Tests for the timezone store, timezone helpers, and the timezone API route."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.routes import meta as meta_routes
from server.services.timezone_store import TimezoneStore
from server.utils import timezones

UTC = timezone.utc


@pytest.fixture
def store(tmp_path):
    return TimezoneStore(tmp_path / "timezone.txt")


@pytest.fixture
def user_store(store, monkeypatch):
    """Make the helpers and API route use this test's store instead of the global one."""
    monkeypatch.setattr(timezones, "get_timezone_store", lambda: store)
    monkeypatch.setattr(meta_routes, "get_timezone_store", lambda: store)
    return store


# --- TimezoneStore --------------------------------------------------------

def test_new_store_returns_default(store):
    assert store.get_timezone() == "UTC"
    assert store.get_timezone("Europe/Paris") == "Europe/Paris"


def test_set_timezone_persists_across_restarts(store, tmp_path):
    store.set_timezone("  America/New_York  ")
    assert store.get_timezone() == "America/New_York"
    assert TimezoneStore(tmp_path / "timezone.txt").get_timezone() == "America/New_York"


@pytest.mark.parametrize("bad", ["", "   ", None, "Mars/Olympus", "../../etc/passwd"])
def test_set_timezone_rejects_invalid_names(store, bad):
    store.set_timezone("Europe/London")
    with pytest.raises(ValueError):
        store.set_timezone(bad)
    assert store.get_timezone() == "Europe/London"


def test_clear_forgets_timezone(store, tmp_path):
    store.set_timezone("Asia/Tokyo")
    store.clear()
    assert store.get_timezone() == "UTC"
    assert not (tmp_path / "timezone.txt").exists()


def test_blank_file_is_treated_as_unset(tmp_path):
    (tmp_path / "timezone.txt").write_text("   \n")
    assert TimezoneStore(tmp_path / "timezone.txt").get_timezone() == "UTC"


# --- timezone helpers -----------------------------------------------------

def test_convert_to_user_timezone_uses_stored_zone(user_store):
    user_store.set_timezone("America/New_York")
    converted = timezones.convert_to_user_timezone(datetime(2026, 10, 26, 13, 0, tzinfo=UTC))
    assert (converted.hour, converted.tzinfo.key) == (9, "America/New_York")


def test_convert_treats_naive_datetimes_as_utc(user_store):
    user_store.set_timezone("Asia/Tokyo")  # UTC+9
    converted = timezones.convert_to_user_timezone(datetime(2026, 10, 26, 0, 0))
    assert converted.hour == 9


def test_helpers_default_to_utc_when_unset(user_store):
    assert timezones.get_user_timezone_name() == "UTC"
    assert timezones.resolve_user_timezone().key == "UTC"


def test_corrupt_stored_zone_falls_back_to_utc(tmp_path, monkeypatch):
    (tmp_path / "timezone.txt").write_text("Not/AZone")
    corrupt = TimezoneStore(tmp_path / "timezone.txt")
    monkeypatch.setattr(timezones, "get_timezone_store", lambda: corrupt)
    assert timezones.resolve_user_timezone().key == "UTC"


def test_now_in_user_timezone_returns_aware_datetime_or_formatted_string(user_store):
    user_store.set_timezone("Europe/Paris")
    now = timezones.now_in_user_timezone()
    assert now.tzinfo.key == "Europe/Paris"
    assert len(timezones.now_in_user_timezone("%Y-%m-%d")) == len("2026-10-26")


# --- /api/v1/meta/timezone ------------------------------------------------

def test_timezone_route_sets_and_reads_back(user_store):
    with TestClient(app) as client:
        set_response = client.post("/api/v1/meta/timezone", json={"timezone": "America/Chicago"})
        get_response = client.get("/api/v1/meta/timezone")
    assert set_response.status_code == 200
    assert get_response.json()["timezone"] == "America/Chicago"


def test_timezone_route_rejects_unknown_zone(user_store):
    with TestClient(app) as client:
        response = client.post("/api/v1/meta/timezone", json={"timezone": "Mars/Olympus"})
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert user_store.get_timezone() == "UTC"


def test_timezone_route_rejects_missing_field(user_store):
    with TestClient(app) as client:
        response = client.post("/api/v1/meta/timezone", json={})
    assert response.status_code == 422
