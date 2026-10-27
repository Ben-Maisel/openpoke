"""Tests for the notification lanes (P7): now, digest, silent; digest timing and quiet hours (no LLM)."""

from datetime import datetime, time, timedelta, timezone

import pytest

from server.config import Settings
from server.services import notifications as notifications_module
from server.services.notifications import NotificationCenter, in_quiet_hours, parse_quiet_hours, render_digest


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


class Inbox:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)


NOON = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(monkeypatch):
    current = {"value": Settings(digest_interval_minutes=60, quiet_hours="22:00-07:00")}
    monkeypatch.setattr(notifications_module, "get_settings", lambda: current["value"])
    monkeypatch.setattr(notifications_module, "convert_to_user_timezone", lambda dt: dt)  # user in UTC
    return current


@pytest.fixture
def clock():
    return Clock(NOON)


@pytest.fixture
def inbox():
    return Inbox()


@pytest.fixture
def center(tmp_path, clock, inbox, settings):
    return NotificationCenter(tmp_path / "notifications.db", now=clock, deliver=inbox)


# --- Lanes ----------------------------------------------------------------------------

async def test_now_is_delivered_immediately_and_recorded(center, inbox):
    note = await center.notify("[SUCCESS] Dentist (a3): call at 9", lane="now", source="trigger 4")
    assert inbox.messages == ["[SUCCESS] Dentist (a3): call at 9"]
    assert (note.lane, note.source, note.delivered_at) == ("now", "trigger 4", NOON.isoformat())


async def test_silent_is_recorded_but_never_delivered(center, inbox, clock):
    await center.notify("NO_UPDATE", lane="silent", source="trigger 5")
    clock.advance(hours=5)
    assert await center.flush_digest() is False
    assert inbox.messages == []
    assert [n.lane for n in center.history()] == ["silent"]


async def test_unknown_lanes_are_rejected(center):
    with pytest.raises(ValueError, match="Use one of: now, digest, silent"):
        await center.notify("x", lane="urgent", source="s")


# --- The digest --------------------------------------------------------------------------

async def test_digest_waits_until_the_oldest_update_has_waited_the_interval(center, inbox, clock):
    await center.notify("Alice replied", lane="digest", source="trigger 1")
    clock.advance(minutes=40)
    await center.notify("Newsletter from Figma", lane="digest", source="email")
    assert await center.flush_digest() is False  # oldest has waited 40 of 60 minutes
    clock.advance(minutes=20)
    assert await center.flush_digest() is True
    assert inbox.messages == [
        "Digest of background updates (2, held so the user isn't interrupted for each):\n"
        "- Alice replied\n- Newsletter from Figma"
    ]
    assert center.pending_digest() == []
    assert await center.flush_digest() is False  # nothing is sent twice


async def test_many_updates_become_one_message(center, inbox, clock):
    for i in range(12):
        await center.notify(f"update {i}", lane="digest", source="trigger")
        clock.advance(minutes=5)
    await center.flush_digest()
    assert len(inbox.messages) == 1 and inbox.messages[0].count("\n- ") == 12


async def test_digest_is_held_during_quiet_hours_but_now_still_goes(center, inbox, clock):
    clock.now = datetime(2026, 10, 27, 21, 30, tzinfo=timezone.utc)
    await center.notify("Alice replied", lane="digest", source="trigger 1")
    clock.advance(hours=2)  # 23:30: overdue, but quiet
    assert await center.flush_digest() is False
    await center.notify("Reminder: take medication", lane="now", source="trigger 2")
    assert inbox.messages == ["Reminder: take medication"]
    clock.now = datetime(2026, 10, 28, 7, 0, tzinfo=timezone.utc)  # quiet hours end
    assert await center.flush_digest() is True
    assert "Alice replied" in inbox.messages[-1]


async def test_force_flush_ignores_timing_and_quiet_hours(center, inbox, clock):
    clock.now = datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc)
    await center.notify("Alice replied", lane="digest", source="trigger 1")
    assert await center.flush_digest(force=True) is True
    assert len(inbox.messages) == 1


async def test_quiet_hours_can_be_turned_off(center, inbox, clock, settings):
    settings["value"] = Settings(digest_interval_minutes=0, quiet_hours="")
    clock.now = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
    await center.notify("Alice replied", lane="digest", source="trigger 1")
    assert await center.flush_digest() is True


async def test_clear_forgets_everything(center):
    await center.notify("x", lane="digest", source="s")
    center.clear()
    assert center.history() == [] and center.pending_digest() == []


async def test_the_background_loop_delivers_due_digests(center, inbox, clock, settings):
    import asyncio

    settings["value"] = Settings(digest_interval_minutes=0, quiet_hours="", notification_check_interval_seconds=0.01)
    await center.notify("Alice replied", lane="digest", source="trigger 1")
    await center.start()
    await center.start()  # idempotent
    for _ in range(100):
        if inbox.messages:
            break
        await asyncio.sleep(0.01)
    await center.stop()
    await center.stop()
    assert len(inbox.messages) == 1


def test_render_digest_lists_every_item():
    from server.services.notifications import Notification

    items = [Notification(1, "s", "digest", "one", "t", None), Notification(2, "s", "digest", "two", "t", None)]
    assert render_digest(items).splitlines()[1:] == ["- one", "- two"]


# --- Quiet hours ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("22:00-07:00", (time(22), time(7))),
    (" 13:30 - 14:00 ", (time(13, 30), time(14))),
    ("", None),
    ("nonsense", None),
    ("22:00", None),
    ("09:00-09:00", None),  # an empty window means no quiet hours
])
def test_parse_quiet_hours(text, expected):
    assert parse_quiet_hours(text) == expected


@pytest.mark.parametrize("now, quiet", [
    (time(21, 59), False), (time(22, 0), True), (time(2, 0), True), (time(6, 59), True), (time(7, 0), False),
])
def test_quiet_hours_wrap_past_midnight(now, quiet):
    assert in_quiet_hours(now, (time(22), time(7))) is quiet


@pytest.mark.parametrize("now, quiet", [(time(12, 59), False), (time(13), True), (time(13, 59), True), (time(14), False)])
def test_daytime_quiet_hours(now, quiet):
    assert in_quiet_hours(now, (time(13), time(14))) is quiet


def test_no_window_is_never_quiet():
    assert in_quiet_hours(time(3), None) is False
