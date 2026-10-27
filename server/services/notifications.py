"""Notification lanes (P7): background updates reach the user without interrupting them for each one.

Everything the user didn't just ask for (reminders firing, background checks, important
emails) goes through here, in one of three lanes decided by code:

  now      delivered at once, as its own message (reminders the user set, urgent emails)
  digest   held, then delivered together as one message once the oldest has waited
           `digest_interval_minutes`, and never during quiet hours
  silent   recorded but never sent (a background check that found nothing new)

Answers to the user's own requests don't come through here; they go straight back.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from ..config import DATA_DIR, get_settings
from ..logging_config import logger
from ..utils.timezones import convert_to_user_timezone

LANES = ("now", "digest", "silent")
DIGEST_HEADER = "Digest of background updates"

Deliver = Callable[[str], Awaitable[Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Notification:
    id: int
    source: str
    lane: str
    text: str
    created_at: str
    delivered_at: Optional[str]


# "22:00-07:00" -> (22:00, 07:00); blank or malformed means no quiet hours
def parse_quiet_hours(text: str) -> Optional[Tuple[time, time]]:
    try:
        start, end = (time.fromisoformat(part.strip()) for part in text.split("-"))
    except (AttributeError, ValueError):
        if text and text.strip():
            logger.warning(f"Ignoring malformed quiet_hours {text!r}; expected e.g. '22:00-07:00'")
        return None
    return (start, end) if start != end else None


def in_quiet_hours(local: time, window: Optional[Tuple[time, time]]) -> bool:
    if window is None:
        return False
    start, end = window
    return start <= local < end if start < end else local >= start or local < end  # may wrap past midnight


async def _deliver_to_interaction_agent(text: str) -> None:
    from ..agents.interaction_agent.runtime import InteractionAgentRuntime

    await InteractionAgentRuntime().handle_agent_message(text)


class NotificationCenter:
    """Routes background updates into lanes and delivers the digest on schedule."""

    def __init__(self, db_path: Path, *, now: Callable[[], datetime] = _utc_now, deliver: Optional[Deliver] = None):
        self._db_path = db_path
        self._now = now
        self._deliver = deliver or _deliver_to_interaction_agent
        self._lock = threading.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_pending ON notifications (lane, delivered_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds")

    # --- Routing ------------------------------------------------------------------

    async def notify(self, text: str, *, lane: str, source: str) -> Notification:
        """Record an update in its lane; 'now' updates are delivered before this returns."""
        if lane not in LANES:
            raise ValueError(f"Invalid lane {lane!r}. Use one of: {', '.join(LANES)}.")
        delivered_at = self._timestamp() if lane == "now" else None
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO notifications (source, lane, text, created_at, delivered_at) VALUES (?, ?, ?, ?, ?)",
                (source, lane, text, self._timestamp(), delivered_at),
            )
            notification_id = cursor.lastrowid
        logger.info("notification", extra={"lane": lane, "source": source})
        if lane == "now":
            await self._deliver(text)
        return self._get(notification_id)  # type: ignore[arg-type]

    def _get(self, notification_id: int) -> Notification:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        return Notification(**dict(row))

    def history(self) -> List[Notification]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM notifications ORDER BY id").fetchall()
        return [Notification(**dict(row)) for row in rows]

    def pending_digest(self) -> List[Notification]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE lane = 'digest' AND delivered_at IS NULL ORDER BY id"
            ).fetchall()
        return [Notification(**dict(row)) for row in rows]

    # --- The digest -------------------------------------------------------------------

    def is_quiet(self, when: Optional[datetime] = None) -> bool:
        local = convert_to_user_timezone(when or self._now())
        return in_quiet_hours(local.time(), parse_quiet_hours(get_settings().quiet_hours))

    async def flush_digest(self, *, force: bool = False) -> bool:
        """Deliver held updates as one message when the oldest has waited long enough and it isn't quiet hours."""
        items = self.pending_digest()
        if not items:
            return False
        now = self._now()
        if not force:
            oldest = datetime.fromisoformat(items[0].created_at)
            if now - oldest < timedelta(minutes=get_settings().digest_interval_minutes) or self.is_quiet(now):
                return False
        with self._lock, closing(self._connect()) as conn:  # claim them first, so a second flush can't resend
            conn.execute(
                f"UPDATE notifications SET delivered_at = ? WHERE id IN ({', '.join('?' * len(items))})",
                (self._timestamp(), *(item.id for item in items)),
            )
        await self._deliver(render_digest(items))
        return True

    # --- Background loop ---------------------------------------------------------------

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._run(), name="notification-digest")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.flush_digest()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("digest delivery failed", extra={"error": str(exc)})
            await asyncio.sleep(get_settings().notification_check_interval_seconds)

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM notifications")


def render_digest(items: List[Notification]) -> str:
    lines = [f"{DIGEST_HEADER} ({len(items)}, held so the user isn't interrupted for each):"]
    lines += [f"- {item.text}" for item in items]
    return "\n".join(lines)


_center: Optional[NotificationCenter] = None
_center_lock = threading.Lock()


def get_notification_center() -> NotificationCenter:
    global _center
    if _center is None:
        with _center_lock:
            if _center is None:
                _center = NotificationCenter(DATA_DIR / "notifications.db")
    return _center


__all__ = [
    "DIGEST_HEADER", "LANES", "Notification", "NotificationCenter", "get_notification_center",
    "in_quiet_hours", "parse_quiet_hours", "render_digest",
]
