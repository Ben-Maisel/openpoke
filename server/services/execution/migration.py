"""One-time import of agents from before the registry (P1e).

Before P1, agents were a list of names in roster.json, memory lived in
execution_agents/<slugified-name>.log, and triggers were owned by agent name.
This registers each name as an agent, gives it a copy of its memory under its
new ID, and moves its triggers to that ID. roster.json is then renamed so the
import never runs twice.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

from ...config import get_settings
from ...logging_config import logger
from .log_store import ExecutionAgentLogStore, _slugify
from .registry import AgentRegistry



# Import a legacy roster; returns {old name: new agent ID} (empty if there is nothing to import)
def migrate_legacy_roster(agents_dir: Path, registry: AgentRegistry, log_store: ExecutionAgentLogStore,
                          trigger_service) -> Dict[str, str]:
    roster_path = agents_dir / "roster.json"
    if not roster_path.exists():
        return {}
    try:
        names: List[str] = [str(n) for n in json.loads(roster_path.read_text(encoding="utf-8")) if str(n).strip()]
    except Exception as exc:
        logger.warning(f"Legacy roster unreadable; skipping import: {exc}")
        return {}

    mapping: Dict[str, str] = {}
    legacy_logs = set()
    for name in dict.fromkeys(names):  # de-duplicate, keep order
        legacy_log = agents_dir / f"{_slugify(name)}.log"
        agent = registry.create(name, summary=_last_request(log_store, _slugify(name)) or name)
        if legacy_log.exists():
            # Copy, not move: names that slugified alike shared one file, and each keeps that history.
            shutil.copyfile(legacy_log, agents_dir / f"{agent.id}.log")
            legacy_logs.add(legacy_log)
        trigger_service.reassign_agent(name, agent.id)
        mapping[name] = agent.id

    for legacy_log in legacy_logs:
        legacy_log.rename(legacy_log.with_name(legacy_log.name + ".migrated"))
    roster_path.rename(roster_path.with_name("roster.json.migrated"))
    logger.info(f"Imported {len(mapping)} legacy agents into the registry")
    return mapping


def _last_request(log_store: ExecutionAgentLogStore, legacy_key: str) -> str:
    requests = [payload for tag, _, payload in log_store.iter_entries(legacy_key) if tag == "agent_request"]
    return requests[-1][:get_settings().initial_card_chars] if requests else ""


__all__ = ["migrate_legacy_roster"]
