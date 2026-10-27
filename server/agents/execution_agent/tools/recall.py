"""Recall: let an execution agent look back through its own full journal (P5/P6).

The prompt only shows recent runs as digests. `recall` reaches everything else:
by reference it returns one tool call's full stored record; by query it searches every
journal entry (including runs no longer in the prompt) and returns the best matches.
Search is lexical, weighted by how rare each word is in the journal (IDF), with no LLM call.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from functools import partial
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from server.services.execution import get_execution_agent_logs
from server.services.execution.log_store import TOOL_CALL_TAG
from server.services.execution.matching import meaningful_words

from ..memory import digest_tool_call

MAX_MATCHES = 5
MATCH_CHARS = 1500  # per match in a search
RECORD_CHARS = 20_000  # a full record; larger ones are cut (the journal itself never is)

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Look back through your own full history, including runs no longer shown in your prompt. "
                "Pass `ref` (e.g. 'r12') to get one past tool call's complete arguments and result, such as a "
                "whole email that a digest only summarized. Pass `query` to search all your past requests, "
                "tool calls and responses; you get the best matches with their timestamps and refs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "A tool call reference from your history, e.g. 'r12'."},
                    "query": {"type": "string", "description": "Words to search your history for."},
                },
                "additionalProperties": False,
            },
        },
    },
]


def get_schemas() -> List[Dict[str, Any]]:
    """Return the recall tool schema."""

    return _SCHEMAS


def _parse(tag: str, payload: str) -> Optional[Dict[str, Any]]:
    if tag != TOOL_CALL_TAG:
        return None
    try:
        record = json.loads(payload)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _by_ref(agent_id: str, ref: str) -> Dict[str, Any]:
    for tag, timestamp, payload in get_execution_agent_logs().iter_entries(agent_id):
        record = _parse(tag, payload)
        if record and record.get("ref") == ref:
            text = json.dumps(record, ensure_ascii=False, default=str)
            if len(text) <= RECORD_CHARS:
                return {"ref": ref, "timestamp": timestamp, "record": record}
            return {"ref": ref, "timestamp": timestamp, "record": text[:RECORD_CHARS],
                    "note": f"Cut at {RECORD_CHARS} of {len(text)} characters."}
    return {"error": f"No tool call {ref!r} in your history. Refs look like 'r12'; use query to search."}


def _search(agent_id: str, query: str) -> Dict[str, Any]:
    words = meaningful_words(query)
    if not words:
        return {"error": "The query has no searchable words; name a person, subject, or detail."}

    # Searchable text is the full stored content; what's shown is the same digest the prompt uses
    entries: List[Tuple[str, str, str, FrozenSet[str]]] = []
    for tag, timestamp, payload in get_execution_agent_logs().iter_entries(agent_id):
        record = _parse(tag, payload)
        if record and record.get("tool") == "recall":
            continue  # earlier lookups would only echo what they found
        shown = digest_tool_call(record) if record else payload
        entries.append((tag, timestamp, shown, meaningful_words(payload)))

    document_frequency = Counter(word for *_, entry_words in entries for word in entry_words & words)
    scored = []
    for index, (tag, timestamp, shown, entry_words) in enumerate(entries):
        score = sum(math.log(1 + len(entries) / document_frequency[w]) for w in entry_words & words)
        if score > 0:
            scored.append((score, index, tag, timestamp, shown))
    scored.sort(key=lambda item: (-item[0], -item[1]))  # best first; newer wins a tie

    matches = []
    for _, _, tag, timestamp, shown in scored[:MAX_MATCHES]:
        text = shown if len(shown) <= MATCH_CHARS else f"{shown[:MATCH_CHARS]}…"
        matches.append({"timestamp": timestamp, "kind": tag, "text": text})
    return {"query": query, "searched_entries": len(entries), "matches": matches}


def _recall_tool(*, agent_id: str, ref: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
    ref, query = (ref or "").strip(), (query or "").strip()
    if bool(ref) == bool(query):
        return {"error": "Pass exactly one of `ref` (e.g. 'r12') or `query`."}
    return _by_ref(agent_id, ref) if ref else _search(agent_id, query)


def build_registry(agent_id: str) -> Dict[str, Callable[..., Any]]:
    """Return the recall tool bound to a specific agent."""

    return {"recall": partial(_recall_tool, agent_id=agent_id)}


__all__ = ["build_registry", "get_schemas"]
