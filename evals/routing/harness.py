"""Run one routing decision against a real model, using the interaction agent's own prompt code."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape
from typing import Any, Dict, List, Optional, Sequence, Tuple

from server.agents.interaction_agent.agent import build_system_prompt, prepare_message_with_history
from server.agents.interaction_agent.tools import get_tool_schemas
from server.openrouter_client import request_chat_completion

from .cases import RoutingCase

# Stand-in results for the interaction agent's non-routing tools, so the model
# can keep going (e.g. it says "on it" to the user before delegating).
_FAKE_TOOL_RESULTS = {
    "send_message_to_user": {"status": "delivered"},
    "send_draft": {"status": "draft_recorded"},
    "wait": {"status": "waiting"},
}
_HISTORY_TIMESTAMP = "2026-10-26 09:00:00"


@dataclass
class Decision:
    """What the model did for one case."""

    routes: List[str] = field(default_factory=list)  # agent names delegated to, in order
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    hit_step_limit: bool = False
    error: Optional[str] = None
    raw_responses: List[Dict[str, Any]] = field(default_factory=list)  # the model's message at each step, verbatim


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
    max_steps: int = 4,
) -> Decision:
    decision = Decision()
    system = build_system_prompt()
    tools = get_tool_schemas()
    messages: List[Dict[str, Any]] = prepare_message_with_history(
        case.message, render_history(case.history), message_type="user", agents=list(roster)
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

        parsed = _parse_calls(tool_calls)
        routes = [args["agent_name"] for name, args in parsed
                  if name == "send_message_to_agent" and args is not None and args.get("agent_name")]
        if routes:
            decision.routes = routes
            return decision
        if not tool_calls:
            return decision

        # Like the app: answer every call (errors included) and let the model continue.
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls})
        for call, (name, args) in zip(tool_calls, parsed):
            if args is None:
                result = {"error": "invalid json"}
            elif name == "send_message_to_agent":
                result = {"error": "Missing required arguments: agent_name"}
            else:
                result = _FAKE_TOOL_RESULTS.get(name, {"error": f"Unknown tool: {name}"})
            messages.append({"role": "tool", "tool_call_id": call.get("id") or name, "content": json.dumps(result)})

    decision.hit_step_limit = True
    return decision


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
