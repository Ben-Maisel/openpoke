"""Agent registry: a stable ID and a card for every execution agent (P1).

An agent's identity is its ID (`a1`, `a2`, ...), assigned once at creation and
never reused, even after the agent is deleted. The name is only a label. Each
agent has a card: a short summary of what it has done, plus facts recorded by
code from its tool calls (emails, people, threads, drafts, triggers).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from ...config import DATA_DIR
from ...logging_config import logger

FACT_KINDS = ("email", "person", "thread", "draft", "trigger")
STATUSES = ("active", "dormant", "archived")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AgentRecord:
    id: str
    name: str
    summary: str
    status: str
    created_at: str
    last_used_at: str
    run_count: int


class AgentRegistry:
    """SQLite-backed store of agents, their cards, and their facts."""

    def __init__(self, db_path: Path, *, now: Callable[[], datetime] = _utc_now):
        self._db_path = db_path
        self._now = now
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS agents (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,  -- AUTOINCREMENT: numbers are never reused
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    run_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_agents_last_used ON agents (last_used_at);
                CREATE TABLE IF NOT EXISTS agent_facts (
                    seq INTEGER NOT NULL REFERENCES agents(seq) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (seq, kind, value)
                );
                """
            )

    # --- Agents ---------------------------------------------------------------

    def create(self, name: str, summary: str = "") -> AgentRecord:
        name = name.strip()
        if not name:
            raise ValueError("agent name must not be empty")
        timestamp = self._timestamp()
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO agents (name, summary, created_at, last_used_at) VALUES (?, ?, ?, ?)",
                (name, summary.strip(), timestamp, timestamp),
            )
            seq = cursor.lastrowid
        logger.info("registered agent", extra={"agent_id": f"a{seq}", "agent_name": name})
        return self.get(f"a{seq}")  # type: ignore[return-value]

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        seq = _parse_id(agent_id)
        if seq is None:
            return None
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM agents WHERE seq = ?", (seq,)).fetchone()
        return _to_record(row) if row else None

    def find_by_name(self, name: str) -> Optional[AgentRecord]:
        """Exact-name lookup; with several agents of the same name, the most recently used wins."""
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE name = ? ORDER BY last_used_at DESC, seq DESC LIMIT 1",
                (name.strip(),),
            ).fetchone()
        return _to_record(row) if row else None

    def list_agents(self, statuses: Optional[Sequence[str]] = None) -> List[AgentRecord]:
        """Agents ordered most recently used first."""
        sql, params = "SELECT * FROM agents", []
        if statuses:
            sql += f" WHERE status IN ({', '.join('?' * len(statuses))})"
            params = list(statuses)
        sql += " ORDER BY last_used_at DESC, seq DESC"
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_to_record(row) for row in rows]

    def touch(self, agent_id: str) -> bool:
        """Record that the agent was just used (one more run, last used now)."""
        return self._update(agent_id, "last_used_at = ?, run_count = run_count + 1", (self._timestamp(),))

    def update_summary(self, agent_id: str, summary: str) -> bool:
        return self._update(agent_id, "summary = ?", (summary.strip(),))

    def set_status(self, agent_id: str, status: str) -> bool:
        if status not in STATUSES:
            raise ValueError(f"Invalid agent status {status!r}. Use one of: {', '.join(STATUSES)}.")
        return self._update(agent_id, "status = ?", (status,))

    def delete(self, agent_id: str) -> bool:
        seq = _parse_id(agent_id)
        if seq is None:
            return False
        with self._lock, closing(self._connect()) as conn:
            return conn.execute("DELETE FROM agents WHERE seq = ?", (seq,)).rowcount > 0

    def clear(self) -> None:
        """Remove every agent. IDs are still never reused afterwards."""
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM agents")

    # --- Facts ----------------------------------------------------------------

    def add_facts(self, agent_id: str, kind: str, values: Iterable[str]) -> int:
        """Attach facts of one kind; duplicates and blanks are ignored. Returns how many were new."""
        if kind not in FACT_KINDS:
            raise ValueError(f"Invalid fact kind {kind!r}. Use one of: {', '.join(FACT_KINDS)}.")
        seq = _parse_id(agent_id)
        cleaned = sorted({v.strip() for v in values if v and v.strip()})
        if seq is None or not cleaned:
            return 0
        with self._lock, closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM agents WHERE seq = ?", (seq,)).fetchone() is None:
                return 0
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO agent_facts (seq, kind, value) VALUES (?, ?, ?)",
                [(seq, kind, value) for value in cleaned],
            )
            return conn.total_changes - before

    def remove_fact(self, agent_id: str, kind: str, value: str) -> bool:
        seq = _parse_id(agent_id)
        if seq is None:
            return False
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                "DELETE FROM agent_facts WHERE seq = ? AND kind = ? AND value = ?", (seq, kind, value)
            )
            return cursor.rowcount > 0

    def facts(self, agent_id: str) -> Dict[str, List[str]]:
        """All facts for an agent, grouped by kind (every kind present, possibly empty)."""
        grouped: Dict[str, List[str]] = {kind: [] for kind in FACT_KINDS}
        seq = _parse_id(agent_id)
        if seq is None:
            return grouped
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT kind, value FROM agent_facts WHERE seq = ? ORDER BY kind, value", (seq,)
            ).fetchall()
        for row in rows:
            grouped[row["kind"]].append(row["value"])
        return grouped

    def all_facts(self) -> Dict[str, Dict[str, List[str]]]:
        """Facts for every agent in one query (agent id -> kind -> values); agents without facts are omitted."""
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT seq, kind, value FROM agent_facts ORDER BY seq, kind, value").fetchall()
        grouped: Dict[str, Dict[str, List[str]]] = {}
        for row in rows:
            grouped.setdefault(f"a{row['seq']}", {kind: [] for kind in FACT_KINDS})[row["kind"]].append(row["value"])
        return grouped

    # --- Helpers --------------------------------------------------------------

    def _update(self, agent_id: str, assignments: str, params: tuple) -> bool:
        seq = _parse_id(agent_id)
        if seq is None:
            return False
        with self._lock, closing(self._connect()) as conn:
            return conn.execute(f"UPDATE agents SET {assignments} WHERE seq = ?", (*params, seq)).rowcount > 0

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_id(agent_id: str) -> Optional[int]:
    """'a17' -> 17. Anything else (wrong prefix, not a number, blank) -> None."""
    text = (agent_id or "").strip().lower()
    if len(text) < 2 or text[0] != "a" or not text[1:].isdigit():
        return None
    return int(text[1:])


def _to_record(row: sqlite3.Row) -> AgentRecord:
    return AgentRecord(
        id=f"a{row['seq']}",
        name=row["name"],
        summary=row["summary"],
        status=row["status"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        run_count=row["run_count"],
    )


_agent_registry: Optional[AgentRegistry] = None
_registry_lock = threading.Lock()


def get_agent_registry() -> AgentRegistry:
    global _agent_registry
    if _agent_registry is None:
        with _registry_lock:
            if _agent_registry is None:
                _agent_registry = AgentRegistry(DATA_DIR / "execution_agents" / "registry.db")
    return _agent_registry


__all__ = ["AgentRecord", "AgentRegistry", "FACT_KINDS", "STATUSES", "get_agent_registry"]
