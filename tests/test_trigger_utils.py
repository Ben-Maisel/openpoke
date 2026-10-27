"""Unit tests for trigger time/recurrence helpers (no I/O)."""

from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from server.services.triggers.utils import (
    UTC_ZONE_KEYS,
    build_recurrence,
    coerce_start_datetime,
    load_rrule,
    normalize_status,
    parse_datetime,
    parse_iso,
    resolve_timezone,
    to_storage_timestamp,
)

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")


# --- to_storage_timestamp -------------------------------------------------

def test_storage_timestamp_converts_to_utc_z_format():
    local = datetime(2026, 10, 26, 9, 0, tzinfo=NEW_YORK)  # EDT, UTC-4
    assert to_storage_timestamp(local) == "2026-10-26T13:00:00Z"


def test_storage_timestamp_drops_microseconds():
    moment = datetime(2026, 10, 26, 13, 0, 0, 999_999, tzinfo=UTC)
    assert to_storage_timestamp(moment) == "2026-10-26T13:00:00Z"


# --- resolve_timezone -----------------------------------------------------

def test_resolve_timezone_returns_named_zone():
    assert resolve_timezone("America/New_York").key == "America/New_York"


@pytest.mark.parametrize("name", [None, "", "Not/AZone"])
def test_resolve_timezone_falls_back_to_utc(name):
    assert resolve_timezone(name).key == "UTC"


# --- normalize_status -----------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [(None, "active"), ("", "active"), ("active", "active"), ("PAUSED", "paused"), ("Completed", "completed")],
)
def test_normalize_status_accepts_known_values(raw, expected):
    assert normalize_status(raw) == expected


# --- parse_iso / parse_datetime / coerce_start_datetime -------------------

def test_parse_iso_assumes_utc_when_naive():
    assert parse_iso("2026-10-26T09:00:00") == datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def test_parse_iso_keeps_explicit_offset():
    parsed = parse_iso("2026-10-26T09:00:00-04:00")
    assert parsed == datetime(2026, 10, 26, 13, 0, tzinfo=UTC)


def test_parse_iso_rejects_garbage():
    with pytest.raises(ValueError):
        parse_iso("next tuesday-ish")


def test_parse_datetime_interprets_naive_time_in_given_zone():
    parsed = parse_datetime("2026-10-26T09:00:00", NEW_YORK)
    assert parsed.astimezone(UTC) == datetime(2026, 10, 26, 13, 0, tzinfo=UTC)


def test_parse_datetime_converts_aware_time_into_given_zone():
    parsed = parse_datetime("2026-10-26T13:00:00Z", NEW_YORK)
    assert (parsed.hour, parsed.tzinfo) == (9, NEW_YORK)


def test_coerce_start_uses_fallback_when_no_start_given():
    fallback = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    assert coerce_start_datetime(None, NEW_YORK, fallback) == fallback


# --- build_recurrence -----------------------------------------------------

START_NY = datetime(2026, 10, 26, 9, 0, tzinfo=NEW_YORK)


def test_build_recurrence_returns_none_without_rule():
    assert build_recurrence(None, START_NY, NEW_YORK) is None


def test_build_recurrence_adds_rrule_prefix_and_tzid_dtstart():
    text = build_recurrence("FREQ=DAILY", START_NY, NEW_YORK)
    assert text == "DTSTART;TZID=America/New_York:20261026T090000\nRRULE:FREQ=DAILY"


def test_build_recurrence_uses_z_dtstart_for_utc():
    start = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
    text = build_recurrence("RRULE:FREQ=WEEKLY", start, ZoneInfo("UTC"))
    assert text == "DTSTART:20261026T090000Z\nRRULE:FREQ=WEEKLY"


@pytest.mark.parametrize("zone", ["Europe/London", "Europe/Lisbon", "Europe/Dublin", "Atlantic/Canary"])
def test_build_recurrence_keeps_tzid_for_zones_that_are_only_zero_offset_today(zone):
    # Winter start: the UTC offset is 0, but the zone is not UTC (P21).
    tz = ZoneInfo(zone)
    start = datetime(2026, 11, 2, 9, 0, tzinfo=tz)
    assert build_recurrence("FREQ=DAILY", start, tz).startswith(f"DTSTART;TZID={zone}:20261102T090000")


@pytest.mark.parametrize("zone", sorted(UTC_ZONE_KEYS))
def test_utc_zone_list_only_contains_zones_that_are_utc_all_year(zone):
    tz = ZoneInfo(zone)
    for month in (1, 7):
        assert datetime(2026, month, 15, tzinfo=tz).utcoffset().total_seconds() == 0
    assert build_recurrence("FREQ=DAILY", datetime(2026, 7, 15, 9, tzinfo=tz), tz).startswith("DTSTART:20260715T090000Z")


def test_build_recurrence_replaces_caller_supplied_dtstart():
    text = build_recurrence("DTSTART:19990101T000000Z\nRRULE:FREQ=DAILY", START_NY, NEW_YORK)
    assert "1999" not in text
    assert text.startswith("DTSTART;TZID=America/New_York:20261026T090000")


def test_build_recurrence_rejects_rule_with_only_dtstart():
    with pytest.raises(ValueError):
        build_recurrence("DTSTART:20261026T090000Z", START_NY, NEW_YORK)


def test_load_rrule_rejects_invalid_rule():
    with pytest.raises(ValueError):
        load_rrule("DTSTART:20261026T090000Z\nRRULE:FREQ=SOMETIMES")
