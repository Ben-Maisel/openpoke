"""Keep each agent's card summary current: one short LLM call after every successful run (P1d)."""

from __future__ import annotations

import asyncio
from typing import Optional, Set

from ...config import get_settings
from ...logging_config import logger
from ...openrouter_client import request_chat_completion
from ...services.execution import get_agent_registry

CARD_SYSTEM_PROMPT = (
    "You maintain a status card for one background worker of a personal assistant. The assistant reads "
    "these cards to decide which worker should handle a new request, so the card must say what this worker "
    "is for and where things stand. The worker also reads its own card as its long-term memory: its prompt "
    "only shows its most recent runs, so anything older survives only through this card.\n"
    "Write at most four sentences (under 550 characters): the worker's topic, the current state, anything "
    "still open (drafts awaiting approval, replies being waited on, scheduled reminders), and what earlier "
    "runs settled that still matters (decisions, what was sent, the user's stated preferences). Carry those "
    "forward from the previous card unless the latest run changed them. Keep concrete names, people, dates, "
    "and IDs. Plain text only, no preamble."
)

# Keep references so fire-and-forget refreshes aren't garbage-collected mid-flight
_background_refreshes: Set["asyncio.Task[Optional[str]]"] = set()


# Rewrite an agent's card summary from its previous card, the latest request, and its report
async def refresh_card_summary(agent_id: str, request: str, response: str) -> Optional[str]:
    registry = get_agent_registry()
    record = registry.get(agent_id)
    if record is None:
        return None
    settings = get_settings()
    prompt = (
        f"Worker name: {record.name}\n"
        f"Previous card: {record.summary or '(none)'}\n"
        f"Latest request: {request.strip()}\n"
        f"Worker's report: {response.strip()}\n\n"
        "Write the updated card."
    )
    try:
        reply = await request_chat_completion(
            model=settings.summarizer_model,
            messages=[{"role": "user", "content": prompt}],
            system=CARD_SYSTEM_PROMPT,
            api_key=settings.openrouter_api_key,
        )
        summary = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception as exc:  # the old summary stays
        logger.warning(f"[{agent_id}] Card refresh failed: {exc}")
        return None

    summary = " ".join(summary.split())[:settings.card_max_chars]
    if not summary:
        return None
    registry.update_summary(agent_id, summary)
    return summary


# Start a refresh in the background so it never delays the agent's report
def schedule_card_refresh(agent_id: str, request: str, response: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(refresh_card_summary(agent_id, request, response))
    _background_refreshes.add(task)
    task.add_done_callback(_background_refreshes.discard)


__all__ = ["CARD_SYSTEM_PROMPT", "refresh_card_summary", "schedule_card_refresh"]
