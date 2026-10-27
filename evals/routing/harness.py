"""Run one routing decision against a real model, using the interaction agent's own prompt code."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from server.agents.interaction_agent.agent import build_system_prompt, prepare_message_with_history, routing_query
from server.agents.interaction_agent.tools import agent_search_results, duplicate_warning, get_tool_schemas
from server.openrouter_client import request_chat_completion
from server.services.execution.matching import find_possible_duplicates
from server.services.execution.registry import AgentRecord
from server.services.execution.search import select_working_set

from .cases import RoutingCase
from .scoring import NEW_AGENT_PREFIX

# Stand-in results for the interaction agent's non-routing tools, so the model
# can keep going (e.g. it says "on it" to the user before delegating).
_FAKE_TOOL_RESULTS = {
    "send_message_to_user": {"status": "delivered"},
    "send_draft": {"status": "draft_recorded"},
    "wait": {"status": "waiting"},
}
_HISTORY_TIMESTAMP = "2026-10-26 09:00:00"
_ROSTER_EPOCH = datetime(2026, 9, 1, tzinfo=timezone.utc)


@dataclass
class Decision:
    """What the model did for one case."""

    routes: List[str] = field(default_factory=list)  # agent names delegated to; created agents are "new:<name>"
    duplicate_warnings: int = 0  # create_agent calls held back by the duplicate check
    searches: int = 0  # search_agents calls
    agents_shown: int = 0  # agents in the prompt's working set
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    hit_step_limit: bool = False
    error: Optional[str] = None
    raw_responses: List[Dict[str, Any]] = field(default_factory=list)  # the model's message at each step, verbatim


# Registry-style records for an eval roster. Roster order stands in for creation order, and
# later agents count as more recently used, so the hot cache is the end of the list.
def roster_records(roster: Sequence[str], summaries: Mapping[str, str]) -> List[AgentRecord]:
    records = []
    for index, name in enumerate(roster, start=1):
        used = (_ROSTER_EPOCH + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        records.append(AgentRecord(id=f"a{index}", name=name, summary=summaries.get(name, ""), status="active",
                                   created_at=used, last_used_at=used, run_count=1))
    return records


# Render (tag, text) history entries exactly as ConversationLog.load_transcript does
def render_history(history: Sequence[Tuple[str, str]]) -> str:
    return "\n".join(
        f'<{tag} timestamp="{_HISTORY_TIMESTAMP}">{escape(text, quote=False)}</{tag}>' for tag, text in history
    )


# Ask the model to handle `case.message` with `roster` as the active agents; record where it routes
async def run_decision(
    case: RoutingCase,
    roster: Sequence[str],
    *,
    model: str,
    api_key: Optional[str],
    max_steps: int = 5,
    summaries: Optional[Mapping[str, str]] = None,
) -> Decision:
    decision = Decision()
    system = build_system_prompt()
    tools = get_tool_schemas()
    names_by_id = {f"a{i}": name for i, name in enumerate(roster, start=1)}
    records = roster_records(roster, summaries or {})
    facts: Dict[str, Dict[str, List[str]]] = {}
    history = render_history(case.history)
    # The same working set the app would build: recent agents plus the best matches, with cards.
    working = select_working_set(routing_query(case.message, history), records, facts)
    decision.agents_shown = len(working.agents)
    messages: List[Dict[str, Any]] = prepare_message_with_history(
        case.message, history, message_type="user", working_set=working, facts_by_id=facts
    )

    for _ in range(max_steps):
        decision.steps += 1
        try:
            response = await request_chat_completion(
                model=model, messages=messages, system=system, api_key=api_key, tools=tools
            )
        except Exception as exc:  # network/provider failure: report, don't score
            decision.error = f"{type(exc).__name__}: {exc}"
            return decision
        _add_usage(decision, response.get("usage") or {})

        choices = response.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}
        decision.raw_responses.append(message)
        tool_calls = message.get("tool_calls") or []

        outcomes = [_simulate_tool(name, args, names_by_id, records, facts, decision)
                    for name, args in _parse_calls(tool_calls)]
        routes = [route for _, route in outcomes if route]
        if routes:
            decision.routes = routes
            return decision
        if not tool_calls:
            return decision

        # Like the app: answer every call (errors and duplicate warnings included) and let the model continue.
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call, (result, _) in zip(tool_calls, outcomes):
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "call", "content": json.dumps(result)})

    decision.hit_step_limit = True
    return decision


# What the app would answer to one tool call, and where it routed (None if it didn't route).
# Mirrors server/agents/interaction_agent/tools.py, including the real duplicate check.
def _simulate_tool(
    name: str,
    args: Optional[Dict[str, Any]],
    names_by_id: Dict[str, str],
    records: Sequence[AgentRecord],
    facts: Dict[str, Dict[str, List[str]]],
    decision: Decision,
) -> Tuple[Dict[str, Any], Optional[str]]:
    if args is None:
        return {"error": "invalid json"}, None
    if name == "search_agents":
        decision.searches += 1
        return agent_search_results(str(args.get("query") or ""), list(records), facts), None
    if name == "send_message_to_agent":
        agent_id = str(args.get("agent_id") or "").strip().lower()
        if agent_id in names_by_id:
            return {"status": "submitted", "agent_id": agent_id, "agent_name": names_by_id[agent_id],
                    "new_agent_created": False}, names_by_id[agent_id]
        return {"error": f"No agent with id {agent_id!r}. Use an id from <active_agents>, "
                         "or create_agent for a new task."}, None
    if name == "create_agent":
        new_name = str(args.get("name") or "").strip()
        if not new_name:
            return {"error": "Missing required arguments: name"}, None
        if not args.get("confirm_new"):
            candidates = find_possible_duplicates(new_name, records)
            if candidates:
                decision.duplicate_warnings += 1
                return duplicate_warning(candidates), None
        return {"status": "submitted", "agent_id": "new", "agent_name": new_name,
                "new_agent_created": True}, NEW_AGENT_PREFIX + new_name
    return _FAKE_TOOL_RESULTS.get(name, {"error": f"Unknown tool: {name}"}), None


# Parse tool calls into (name, args); args is None when the model sent unparseable arguments
def _parse_calls(tool_calls: List[Dict[str, Any]]) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    parsed: List[Tuple[str, Optional[Dict[str, Any]]]] = []
    for call in tool_calls:
        function = call.get("function") or {}
        raw = function.get("arguments") or {}
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            args = None
        parsed.append((function.get("name") or "", args if isinstance(args, dict) else None))
    return parsed


def _add_usage(decision: Decision, usage: Dict[str, Any]) -> None:
    decision.prompt_tokens += int(usage.get("prompt_tokens") or 0)
    decision.completion_tokens += int(usage.get("completion_tokens") or 0)
    decision.cost += float(usage.get("cost") or 0.0)
