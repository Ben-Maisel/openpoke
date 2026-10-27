"""Draft store: the exact text of every email draft, and where it is in its approval (P16, P20).

Drafts are recorded by code when a worker's `gmail_create_draft` succeeds, so what the
user is shown, approves, and sends is always the stored text, never an LLM's retelling.
A draft moves open -> shown -> approved -> sent, or to deleted before it is sent, and
the store refuses any other move.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from ...config import DATA_DIR
from ...logging_config import logger

STATUSES = ("open", "shown", "approved", "sent", "deleted")

# Which statuses each move may start from
_ALLOWED_FROM = {
    "shown": ("open", "shown", "approved"),
    "approved": ("shown", "approved"),
    "sent": ("approved",),
    "deleted": ("open", "shown", "approved"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DraftStateError(ValueError):
    """A draft can't make the requested move from its current status."""


@dataclass(frozen=True)
class DraftRecord:
    id: str
    agent_id: str
    recipient: str
    cc: Tuple[str, ...]
    bcc: Tuple[str, ...]
    subject: str
    body: str
    thread_id: Optional[str]
    status: str
    created_at: str
    shown_at: Optional[str]
    approved_at: Optional[str]
    approval_text: Optional[str]
    sent_at: Optional[str]

    def render(self) -> str:
        """The draft as the user sees it."""
        lines = [f"To: {self.recipient}"]
        if self.cc:
            lines.append(f"Cc: {', '.join(self.cc)}")
        if self.bcc:
            lines.append(f"Bcc: {', '.join(self.bcc)}")
        lines.append(f"Subject: {self.subject}")
        return "\n".join(lines) + f"\n\n{self.body}"


class DraftStore:
    """SQLite-backed store of email drafts and their approval state."""

    def __init__(self, db_path: Path, *, now: Callable[[], datetime] = _utc_now):
        self._db_path = db_path
        self._now = now
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,               -- Gmail's draft ID
                    agent_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    cc TEXT NOT NULL DEFAULT '[]',     -- JSON lists
                    bcc TEXT NOT NULL DEFAULT '[]',
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    thread_id TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    shown_at TEXT,
                    approved_at TEXT,
                    approval_text TEXT,
                    sent_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_drafts_agent ON drafts (agent_id, status);
                """
            )

    def _timestamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    # --- Recording --------------------------------------------------------------

    def record(
        self,
        draft_id: str,
        *,
        agent_id: str,
        recipient: str,
        subject: str,
        body: str,
        cc: Iterable[str] = (),
        bcc: Iterable[str] = (),
        thread_id: Optional[str] = None,
    ) -> DraftRecord:
        """Store a new draft as open. Recording the same ID again replaces its text and restarts approval."""
        draft_id = draft_id.strip()
        if not draft_id:
            raise ValueError("draft id must not be empty")
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drafts (id, agent_id, recipient, cc, bcc, subject, body, thread_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (draft_id, agent_id, recipient, json.dumps(list(cc)), json.dumps(list(bcc)),
                 subject, body, thread_id, self._timestamp()),
            )
        logger.info("recorded draft", extra={"draft_id": draft_id, "agent_id": agent_id})
        return self.get(draft_id)  # type: ignore[return-value]

    def get(self, draft_id: str) -> Optional[DraftRecord]:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id.strip(),)).fetchone()
        return _to_record(row) if row else None

    def list_drafts(self, agent_id: Optional[str] = None, statuses: Optional[Sequence[str]] = None) -> List[DraftRecord]:
        """Drafts, newest first, optionally for one agent and/or in some statuses."""
        clauses: List[str] = []
        params: List[str] = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if statuses:
            clauses.append(f"status IN ({', '.join('?' * len(statuses))})")
            params.extend(statuses)
        sql = "SELECT * FROM drafts" + (f" WHERE {' AND '.join(clauses)}" if clauses else "")
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC", params).fetchall()
        return [_to_record(row) for row in rows]

    # --- Moving through approval ---------------------------------------------------

    def mark_shown(self, draft_id: str) -> DraftRecord:
        return self._move(draft_id, "shown", "shown_at = COALESCE(shown_at, ?)", (self._timestamp(),))

    def approve(self, draft_id: str, approval_text: str) -> DraftRecord:
        return self._move(draft_id, "approved", "approved_at = ?, approval_text = ?",
                          (self._timestamp(), approval_text.strip()))

    def mark_sent(self, draft_id: str) -> DraftRecord:
        return self._move(draft_id, "sent", "sent_at = ?", (self._timestamp(),))

    def mark_deleted(self, draft_id: str) -> DraftRecord:
        return self._move(draft_id, "deleted", "", ())

    def _move(self, draft_id: str, status: str, assignments: str, params: tuple) -> DraftRecord:
        allowed = _ALLOWED_FROM[status]
        sets = "status = ?" + (f", {assignments}" if assignments else "")
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT status FROM drafts WHERE id = ?", (draft_id.strip(),)).fetchone()
            if row is None:
                raise DraftStateError(f"No draft {draft_id!r} on record.")
            if row["status"] not in allowed:
                raise DraftStateError(
                    f"Draft {draft_id!r} is {row['status']}; it can only become {status} from: {', '.join(allowed)}."
                )
            conn.execute(f"UPDATE drafts SET {sets} WHERE id = ?", (status, *params, draft_id.strip()))
        return self.get(draft_id)  # type: ignore[return-value]

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM drafts")


def _to_record(row: sqlite3.Row) -> DraftRecord:
    return DraftRecord(
        id=row["id"], agent_id=row["agent_id"], recipient=row["recipient"],
        cc=tuple(json.loads(row["cc"])), bcc=tuple(json.loads(row["bcc"])),
        subject=row["subject"], body=row["body"], thread_id=row["thread_id"], status=row["status"],
        created_at=row["created_at"], shown_at=row["shown_at"], approved_at=row["approved_at"],
        approval_text=row["approval_text"], sent_at=row["sent_at"],
    )


_draft_store: Optional[DraftStore] = None
_store_lock = threading.Lock()


def get_draft_store() -> DraftStore:
    global _draft_store
    if _draft_store is None:
        with _store_lock:
            if _draft_store is None:
                _draft_store = DraftStore(DATA_DIR / "execution_agents" / "drafts.db")
    return _draft_store


__all__ = ["DraftRecord", "DraftStateError", "DraftStore", "STATUSES", "get_draft_store"]
