"""Ask one question of a long-lived worker, through the app's real execution runtime.

Two conditions, same runtime loop, same model, same journal:
  before  the original app: its system prompt (data/legacy_system_prompt.md, from upstream main) and
          its whole journal, with tool arguments cut to 200 characters and results to 500, no recall
  after   the current app: card summary, facts, the budgeted digest history, and the recall tool

Live tools (Gmail, triggers) raise "unavailable" in both, so answers have to come from memory.
Card refreshes are switched off while asking, so every question starts from the same state.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from server.agents.execution_agent import card as card_module
from server.agents.execution_agent import runtime as runtime_module
from server.agents.execution_agent.card import refresh_card_summary
from server.agents.execution_agent.facts import extract_facts
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.openrouter_client import request_chat_completion
from server.services import get_agent_registry, get_execution_agent_logs
from server.services.execution import log_store as log_store_module

from .dataset import DATA_DIR, Question, Run

CONDITIONS = ("before", "after")
LEGACY_PROMPT = (DATA_DIR / "legacy_system_prompt.md").read_text(encoding="utf-8").strip()
INITIAL_CARD_CHARS = 500  # a new agent's card starts as its first instructions, as in the app
CARD_RETRIES = 3
_JOURNAL_START = datetime(2026, 10, 1, 9, 0)

_responses: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("memory_eval_responses", default=None)


@dataclass
class Answer:
    text: str = ""
    error: Optional[str] = None
    steps: int = 0
    tools_called: List[str] = field(default_factory=list)
    first_prompt_tokens: int = 0  # the prompt the worker starts from: its memory, before any recall
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    system_prompt_chars: int = 0
    raw_responses: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def recalls(self) -> int:
        return self.tools_called.count("recall")


# --- Plumbing: record every model response; no card refreshes while asking ----------

async def _recorded_completion(**kwargs: Any) -> Dict[str, Any]:
    response = await request_chat_completion(**kwargs)
    sink = _responses.get()
    if sink is not None:
        sink.append(response)
    return response


@contextmanager
def eval_patches() -> Iterator[None]:
    saved = (runtime_module.request_chat_completion, runtime_module.schedule_card_refresh,
             card_module.request_chat_completion)
    runtime_module.request_chat_completion = _recorded_completion
    runtime_module.schedule_card_refresh = lambda *args, **kwargs: None
    card_module.request_chat_completion = _recorded_completion
    try:
        yield
    finally:
        (runtime_module.request_chat_completion, runtime_module.schedule_card_refresh,
         card_module.request_chat_completion) = saved


@contextmanager
def _clock() -> Iterator[Callable[[str], None]]:
    """Let the journal be written with the story's timestamps instead of today's."""
    saved = log_store_module.now_in_user_timezone
    current = {"time": ""}
    log_store_module.now_in_user_timezone = lambda fmt=None: current["time"]
    try:
        yield lambda stamp: current.update(time=stamp)
    finally:
        log_store_module.now_in_user_timezone = saved


def run_times(count: int) -> List[str]:
    return [(_JOURNAL_START + timedelta(hours=3 * i)).strftime("%Y-%m-%d %H:%M:%S") for i in range(count)]


# --- Before: the original app's prompt ------------------------------------------------

def _legacy_entries(runs: Sequence[Run]) -> List[Tuple[str, str, str]]:
    """The journal exactly as the original app wrote it."""
    entries = []
    for run, stamp in zip(runs, run_times(len(runs))):
        entries.append(("agent_request", stamp, run.request))
        for call in run.calls:
            if call.tool == "createTrigger" and call.ok:  # the trigger tool wrote its own line first
                entries.append(("agent_action", stamp, f"createTrigger succeeded | trigger_id={call.result['trigger_id']}"))
            arguments = json.dumps(call.arguments, default=str)
            result = json.dumps(call.result, default=str) if call.ok else str(call.result)
            entries.append(("agent_action", stamp, f"Calling {call.tool} with: {arguments[:200]}"))
            entries.append(("tool_response", stamp, f"{call.tool}: {result[:500]}"))
        entries.append(("agent_response", stamp, run.response))
    return entries


def legacy_system_prompt(name: str, runs: Sequence[Run], question: str) -> str:
    base = LEGACY_PROMPT.format(agent_name=name, agent_purpose=f"Handle tasks related to: {name}")
    entries = _legacy_entries(runs) + [("agent_request", run_times(len(runs) + 1)[-1], question)]
    transcript = "\n".join(f"<{tag} timestamp=\"{stamp}\">{escape(text, quote=False)}</{tag}>"
                           for tag, stamp, text in entries)
    return f"{base}\n\n# Execution History\n\n{transcript}"


# --- After: the current app's memory, written through its real stores ------------------

def prepare_worker(name: str, runs: Sequence[Run], card: str, question: str) -> str:
    """Create a worker whose journal, facts and card are what the app would hold after these runs."""
    registry, logs = get_agent_registry(), get_execution_agent_logs()
    agent = registry.create(name, summary=card)
    with _clock() as set_time:
        for run, stamp in zip(runs, run_times(len(runs))):
            set_time(stamp)
            logs.record_request(agent.id, run.request)
            for call in run.calls:
                logs.record_tool_call(agent.id, call.tool, call.arguments, call.ok, call.result)
                if call.ok:
                    changes = extract_facts(call.tool, call.arguments, call.result)
                    for kind, values in changes.add.items():
                        registry.add_facts(agent.id, kind, values)
                    for kind, values in changes.remove.items():
                        for value in values:
                            registry.remove_fact(agent.id, kind, value)
            logs.record_agent_response(agent.id, run.response)
        set_time(run_times(len(runs) + 1)[-1])
        logs.record_request(agent.id, question)  # the app records the request when the run starts
    return agent.id


async def build_card(name: str, runs: Sequence[Run]) -> List[str]:
    """Run the app's real card refresh after every run, in order; returns the card after each run."""
    registry = get_agent_registry()
    agent = registry.create(f"{name} (card builder)", summary=runs[0].request.strip()[:INITIAL_CARD_CHARS])
    trail = []
    for run in runs:
        for _ in range(CARD_RETRIES):
            if await refresh_card_summary(agent.id, run.request, run.response) is not None:
                break
        else:
            raise RuntimeError(f"Card refresh kept failing at run {len(trail) + 1}")
        trail.append(registry.get(agent.id).summary)
    return trail


# --- Asking ---------------------------------------------------------------------------------

def _unavailable(**_: Any) -> Any:
    raise RuntimeError("Service unavailable")


async def ask(question: Question, runs: Sequence[Run], condition: str, *, name: str, card: str,
              model: str, api_key: Optional[str]) -> Answer:
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition {condition!r}")
    agent_id = prepare_worker(name, runs, card, question.question) if condition == "after" \
        else get_agent_registry().create(name).id

    runtime = ExecutionAgentRuntime(agent_id)
    runtime.model, runtime.api_key = model, api_key or runtime.api_key
    keep_recall = condition == "after"
    runtime.tool_registry = {tool: (fn if tool == "recall" and keep_recall else _unavailable)
                             for tool, fn in runtime.tool_registry.items() if keep_recall or tool != "recall"}
    runtime.tool_schemas = [s for s in runtime.tool_schemas if s["function"]["name"] in runtime.tool_registry]
    if condition == "before":
        legacy = legacy_system_prompt(name, runs, question.question)
        runtime.agent.build_system_prompt_with_history = lambda: legacy

    sink: List[Dict[str, Any]] = []
    token = _responses.set(sink)
    try:
        system_prompt = runtime.agent.build_system_prompt_with_history()
        result = await runtime.execute(question.question)
    finally:
        _responses.reset(token)

    answer = Answer(text=result.response if result.success else "", error=None if result.success else result.error,
                    steps=len(sink), system_prompt_chars=len(system_prompt), raw_responses=sink)
    for response in sink:
        usage = response.get("usage") or {}
        answer.prompt_tokens += usage.get("prompt_tokens", 0)
        answer.completion_tokens += usage.get("completion_tokens", 0)
        answer.cost += usage.get("cost", 0.0) or 0.0
        message = ((response.get("choices") or [{}])[0].get("message") or {})
        answer.tools_called += [c.get("function", {}).get("name", "") for c in message.get("tool_calls") or []]
    if sink:
        answer.first_prompt_tokens = (sink[0].get("usage") or {}).get("prompt_tokens", 0)
    return answer


__all__ = ["Answer", "CONDITIONS", "ask", "build_card", "eval_patches", "legacy_system_prompt",
           "prepare_worker", "run_times"]
