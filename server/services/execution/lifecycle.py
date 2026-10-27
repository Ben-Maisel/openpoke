"""Agent lifecycle (P3): agents that are done fade from view on their own, and come back when used.

An agent is archived when it has been idle for `agent_archive_after_days` and has nothing
open (no open drafts, no live reminders). It is decided on read from `last_used_at` and the
agent's facts, so there is no background job and nothing to go stale: setting a reminder or
using the agent makes it active again at once. Archived agents leave the working set and
the duplicate check, but search still finds them, and nothing is ever deleted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Mapping, Optional, Sequence, Tuple

from ...config import get_settings
from .registry import AgentRecord

FactsById = Mapping[str, Mapping[str, Sequence[str]]]
_OPEN_KINDS = ("draft", "trigger")  # things still in flight keep an agent active however old it is


def _parse(timestamp: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_archived(agent: AgentRecord, facts: Mapping[str, Sequence[str]], *, now: datetime, after_days: int) -> bool:
    if after_days <= 0 or any(facts.get(kind) for kind in _OPEN_KINDS):
        return False
    last_used = _parse(agent.last_used_at)
    return last_used is not None and now - last_used >= timedelta(days=after_days)


# Split agents into those in view and those archived, keeping their order
def split_archived(
    agents: Sequence[AgentRecord],
    facts_by_id: FactsById,
    *,
    now: Optional[datetime] = None,
    after_days: Optional[int] = None,
) -> Tuple[List[AgentRecord], List[AgentRecord]]:
    now = now or datetime.now(timezone.utc)
    after_days = get_settings().agent_archive_after_days if after_days is None else after_days
    active: List[AgentRecord] = []
    archived: List[AgentRecord] = []
    for agent in agents:
        bucket = archived if is_archived(agent, facts_by_id.get(agent.id, {}), now=now, after_days=after_days) else active
        bucket.append(agent)
    return active, archived


__all__ = ["is_archived", "split_archived"]
