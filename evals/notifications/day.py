"""A day in the life (P7): how often the user is interrupted, before and after notification lanes.

A scripted day of background events runs through the real NotificationCenter with a fake
clock, minute by minute, from 07:00 to 08:00 the next morning (all times are the user's
local time). "Before" is the original app: every important email and every trigger run
became its own message. No LLM is involved, so the numbers are exact and reproducible.

    python -m evals.notifications.day     # writes evals/results/notifications-day/report.md
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from server.config import get_settings
from server.services import notifications as notifications_module
from server.services.notifications import DIGEST_HEADER, NotificationCenter

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results" / "notifications-day"
DAY = datetime(2026, 10, 27, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Event:
    at: str  # "HH:MM" on the day; "+HH:MM" for the next morning
    kind: str  # urgent_email, important_email, unimportant_email, reminder, check_news, check_nothing
    text: str

    @property
    def when(self) -> datetime:
        next_day = self.at.startswith("+")
        hours, minutes = (int(part) for part in self.at.lstrip("+").split(":"))
        return DAY + timedelta(days=1 if next_day else 0, hours=hours, minutes=minutes)


def scripted_day() -> List[Event]:
    events = [
        Event("09:10", "urgent_email", "Interview moved to 3pm today"),
        Event("13:40", "urgent_email", "Chase verification code"),
        Event("17:05", "urgent_email", "Wire approval needed before 5pm ET"),
        Event("08:20", "important_email", "Priya: slides due Friday"),
        Event("10:05", "important_email", "Checkup scheduled Nov 18"),
        Event("10:50", "important_email", "Final round next week"),
        Event("12:30", "important_email", "Lease renewal offer"),
        Event("14:15", "important_email", "Wedding RSVP by Dec 1"),
        Event("16:45", "important_email", "Kitchen renovation quote"),
        Event("22:30", "important_email", "Benefits enrollment closes Nov 15"),
        Event("07:45", "unimportant_email", "Amazon: package shipped"),
        Event("08:00", "unimportant_email", "Morning Brew newsletter"),
        Event("11:15", "unimportant_email", "J.Crew: 40% off today"),
        Event("15:30", "unimportant_email", "LinkedIn: 12 searches"),
        Event("19:20", "unimportant_email", "Uber receipt"),
        Event("09:00", "reminder", "Reminder: call the dentist"),
        Event("18:00", "reminder", "Reminder: take medication"),
    ]
    # An hourly background check ("has Alice replied?") from 07:00 to 22:00 that finds news once
    for hour in range(7, 23):
        kind = "check_news" if hour == 15 else "check_nothing"
        events.append(Event(f"{hour:02d}:00", kind, "Alice replied: Friday works" if kind == "check_news" else "NO_UPDATE"))
    return sorted(events, key=lambda e: e.when)


# Before: the important-email watcher and every trigger run each messaged the user
def interruptions_before(events: List[Event]) -> int:
    return sum(e.kind != "unimportant_email" for e in events)


LANE_FOR = {"urgent_email": "now", "important_email": "digest", "reminder": "now",
            "check_news": "digest", "check_nothing": "silent"}


@dataclass
class DayResult:
    before: int
    deliveries: List[Tuple[datetime, str]]  # (when, message) for every message the user received
    waits: Dict[str, timedelta]  # how long each digest item waited

    @property
    def after(self) -> int:
        return len(self.deliveries)

    @property
    def digests(self) -> int:
        return sum(text.startswith(DIGEST_HEADER) for _, text in self.deliveries)


async def simulate(db_path: Path) -> DayResult:
    events = scripted_day()
    clock = {"now": DAY + timedelta(hours=7)}
    deliveries: List[Tuple[datetime, str]] = []

    async def deliver(text: str) -> None:
        deliveries.append((clock["now"], text))

    center = NotificationCenter(db_path, now=lambda: clock["now"], deliver=deliver)
    queued: Dict[str, datetime] = {}
    waits: Dict[str, timedelta] = {}
    end = DAY + timedelta(days=1, hours=8)
    pending = list(events)
    while clock["now"] <= end:
        while pending and pending[0].when <= clock["now"]:
            event = pending.pop(0)
            lane = LANE_FOR.get(event.kind)
            if lane:
                await center.notify(event.text, lane=lane, source=event.kind)
                if lane == "digest":
                    queued[event.text] = clock["now"]
        if await center.flush_digest():
            for text in list(queued):
                if text in deliveries[-1][1]:
                    waits[text] = clock["now"] - queued.pop(text)
        clock["now"] += timedelta(minutes=1)
    return DayResult(before=interruptions_before(events), deliveries=deliveries, waits=waits)


def run_day() -> DayResult:
    # Times in the script are the user's local time, so skip the timezone conversion
    original = notifications_module.convert_to_user_timezone
    notifications_module.convert_to_user_timezone = lambda dt: dt
    try:
        with tempfile.TemporaryDirectory() as folder:
            return asyncio.run(simulate(Path(folder) / "notifications.db"))
    finally:
        notifications_module.convert_to_user_timezone = original


def write_report(result: DayResult) -> Path:
    settings = get_settings()
    events = scripted_day()
    longest = max(result.waits.values()) if result.waits else timedelta(0)
    daytime_waits = [w for text, w in result.waits.items() if "Benefits" not in text]
    lines = [
        "# A day in the life: interruptions before and after notification lanes",
        "",
        f"One scripted day, run minute by minute through the real notification center (digest every "
        f"{settings.digest_interval_minutes} minutes, quiet hours {settings.quiet_hours}). Deterministic: no LLM.",
        "",
        f"- **Events:** {len(events)}: 15 emails (3 urgent, 7 important, 5 unimportant), 2 reminders, "
        "and an hourly background check from 07:00 to 22:00 that finds news once.",
        "",
        "| | Messages to the user | Of which interrupt at once | Digests |",
        "|---|---:|---:|---:|",
        f"| Before (original app) | {result.before} | {result.before} | 0 |",
        f"| After (lanes) | **{result.after}** | {result.after - result.digests} | {result.digests} |",
        "",
        f"- Longest wait for a digest item during the day: {max(daytime_waits, default=timedelta(0))}. "
        f"The 22:30 email waited {longest} because of quiet hours, and was delivered at 07:00.",
        "- Background checks with nothing new (15 of 16) never reached the user; before, each one was a message.",
        "",
        "## Every message the user received (after)",
        "",
        "| Time | Message |",
        "|---|---|",
    ]
    for when, text in result.deliveries:
        flat = " / ".join(text.splitlines())
        lines.append(f"| {when.strftime('%a %H:%M')} | {flat} |")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:  # pragma: no cover
    result = run_day()
    print(f"before: {result.before} messages; after: {result.after} ({result.digests} digests)")
    print(f"report: {write_report(result)}")


if __name__ == "__main__":  # pragma: no cover
    main()
