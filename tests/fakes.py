"""Test doubles for the LLM (and later Gmail) so agent loops run deterministically offline."""

from __future__ import annotations

import copy
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple, Union

# Every module that imports `request_chat_completion` by name; the fake replaces each one.
LLM_CALL_SITES = [
    "server.agents.interaction_agent.runtime",
    "server.agents.execution_agent.runtime",
    "server.agents.execution_agent.tasks.search_email.tool",
    "server.services.conversation.summarization.summarizer",
    "server.services.gmail.importance_classifier",
]


# --- Building fake model responses ----------------------------------------

def reply(text: str) -> Dict[str, Any]:
    """A model turn that just says something (no tool calls)."""
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def calls(*tool_calls: Tuple[str, Union[Dict[str, Any], str]], text: str = "") -> Dict[str, Any]:
    """A model turn that calls one or more tools.

    Each tool call is (name, arguments). Pass arguments as a str to send raw,
    possibly malformed, JSON exactly as a real model might.
    """
    rendered = []
    for index, (name, arguments) in enumerate(tool_calls):
        raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
        rendered.append({"id": f"call_{index}", "type": "function", "function": {"name": name, "arguments": raw}})
    return {"choices": [{"message": {"role": "assistant", "content": text, "tool_calls": rendered}}]}


# --- The scripted LLM -----------------------------------------------------

@dataclass
class LLMCall:
    """One request the code under test sent to the 'model'."""

    model: str
    system: Optional[str]
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]]

    @property
    def tool_names(self) -> List[str]:
        return [tool["function"]["name"] for tool in self.tools or []]

    @property
    def prompt(self) -> str:
        """The first user message, which is where the agents put their context."""
        return self.messages[0]["content"] if self.messages else ""


Matcher = Callable[[LLMCall], bool]


def is_interaction_agent(call: LLMCall) -> bool:
    return "send_message_to_user" in call.tool_names


def is_execution_agent(call: LLMCall) -> bool:
    return "gmail_create_draft" in call.tool_names


@dataclass
class ScriptedLLM:
    """Replaces the OpenRouter call. Tests queue responses; every request is recorded.

    Responses are queued per matcher, so the interaction agent and execution
    agents can each have their own script even when their calls interleave.
    An Exception in the queue is raised instead of returned.
    """

    calls: List[LLMCall] = field(default_factory=list)
    _queues: List[Tuple[Matcher, Deque[Any]]] = field(default_factory=list)

    def script(self, *responses: Any, when: Matcher = lambda call: True) -> "ScriptedLLM":
        self._queues.append((when, deque(responses)))
        return self

    def for_interaction(self, *responses: Any) -> "ScriptedLLM":
        return self.script(*responses, when=is_interaction_agent)

    def for_execution(self, *responses: Any) -> "ScriptedLLM":
        return self.script(*responses, when=is_execution_agent)

    def calls_to(self, matcher: Matcher) -> List[LLMCall]:
        return [call for call in self.calls if matcher(call)]

    async def __call__(self, *, model, messages, system=None, api_key=None, tools=None, base_url=None):
        # Snapshot the inputs: the runtimes keep appending to the same list.
        call = LLMCall(model=model, system=system, messages=copy.deepcopy(messages), tools=tools)
        self.calls.append(call)
        for matcher, queue in self._queues:
            if queue and matcher(call):
                response = queue.popleft()
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"ScriptedLLM: no scripted response left for this call (tools={call.tool_names})")
