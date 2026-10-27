"""Test doubles for the LLM (and later Gmail) so agent loops run deterministically offline."""

from __future__ import annotations

import asyncio
import base64
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
    "evals.routing.harness",
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


def is_search_task(call: LLMCall) -> bool:
    return "return_search_results" in call.tool_names


def is_agent(name: str) -> Matcher:
    """Matches calls made by one specific execution agent."""
    return lambda call: is_execution_agent(call) and f"Agent Name: {name}\n" in (call.system or "")


@dataclass
class Gated:
    """A scripted response the fake holds back until the test calls `release()`.

    Lets tests control timing (e.g. "this agent is still working") without sleeping.
    """

    response: Any
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    reached: asyncio.Event = field(default_factory=asyncio.Event)

    def release(self) -> None:
        self.gate.set()


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Yield to background tasks until `predicate()` holds, failing after `timeout` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.005)


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

    def for_search(self, *responses: Any) -> "ScriptedLLM":
        return self.script(*responses, when=is_search_task)

    def calls_to(self, matcher: Matcher) -> List[LLMCall]:
        return [call for call in self.calls if matcher(call)]

    async def __call__(self, *, model, messages, system=None, api_key=None, tools=None, base_url=None):
        # Snapshot the inputs: the runtimes keep appending to the same list.
        call = LLMCall(model=model, system=system, messages=copy.deepcopy(messages), tools=tools)
        self.calls.append(call)
        # A real HTTP call always yields to the event loop; without this, background
        # tasks would run in an unrealistically strict order.
        await asyncio.sleep(0)
        for matcher, queue in self._queues:
            if queue and matcher(call):
                response = queue.popleft()
                if isinstance(response, Gated):
                    response.reached.set()
                    await response.gate.wait()
                    response = response.response
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"ScriptedLLM: no scripted response left for this call (tools={call.tool_names})")


# --- Fake Gmail -------------------------------------------------------------

# Every module that imports the Composio Gmail helpers by name; the fake replaces each one.
GMAIL_CALL_SITES = [
    "server.agents.execution_agent.tools.gmail",
    "server.agents.execution_agent.tasks.search_email.tool",
    "server.agents.execution_agent.tasks.search_email.gmail_internal",
    "server.services.gmail.importance_watcher",
]


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


@dataclass
class FakeGmail:
    """In-memory stand-in for the Composio Gmail actions.

    Response shapes approximate Composio's (`{"successful": ..., "data": ...}`);
    the exact live shapes still need confirming against a real account (see P25).
    """

    connected: bool = True
    fail_with: Optional[Exception] = None
    actions: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    inbox: List[Dict[str, Any]] = field(default_factory=list)
    drafts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sent: List[Dict[str, Any]] = field(default_factory=list)

    def add_email(self, message_id: str, *, subject: str, sender: str, body: str,
                  thread_id: Optional[str] = None, timestamp: str = "2026-10-26T12:00:00Z") -> Dict[str, Any]:
        message = {
            "messageId": message_id,
            "threadId": thread_id or f"t-{message_id}",
            "messageTimestamp": timestamp,
            "subject": subject,
            "sender": sender,
            "to": "me@example.com",
            "labelIds": ["INBOX"],
            "messageText": body,
            "payload": {"mimeType": "text/plain", "body": {"data": _b64(body)}},
            "attachmentList": [],
        }
        self.inbox.append(message)
        return message

    def action_names(self) -> List[str]:
        return [name for name, _ in self.actions]

    # The two functions the server imports from server.services.gmail:

    def get_active_gmail_user_id(self) -> Optional[str]:
        return "test-user" if self.connected else None

    def execute_gmail_tool(self, tool_name: str, composio_user_id: str, *, arguments=None) -> Dict[str, Any]:
        arguments = dict(arguments or {})
        self.actions.append((tool_name, arguments))
        if self.fail_with is not None:
            raise self.fail_with
        handler = getattr(self, f"_handle_{tool_name.lower()}", None)
        return handler(arguments) if handler else {"successful": True, "data": {}}

    def _handle_gmail_create_email_draft(self, args):
        draft_id = f"r-{len(self.drafts) + 1}"
        self.drafts[draft_id] = {"id": draft_id, **args}
        return {"successful": True, "data": {"id": draft_id}}

    def _handle_gmail_send_draft(self, args):
        draft = self.drafts.pop(args.get("draft_id"), None)
        if draft is None:
            return {"successful": False, "error": f"Draft {args.get('draft_id')} not found"}
        self.sent.append({"kind": "draft", **draft})
        return {"successful": True, "data": {"id": f"sent-{draft['id']}"}}

    def _handle_gmail_reply_to_thread(self, args):
        self.sent.append({"kind": "reply", **args})
        return {"successful": True, "data": {"id": f"sent-{len(self.sent)}"}}

    def _handle_gmail_forward_message(self, args):
        self.sent.append({"kind": "forward", **args})
        return {"successful": True, "data": {"id": f"sent-{len(self.sent)}"}}

    def _handle_gmail_delete_draft(self, args):
        self.drafts.pop(args.get("draft_id"), None)
        return {"successful": True, "data": {}}

    def _handle_gmail_list_drafts(self, args):
        return {"successful": True, "data": {"drafts": list(self.drafts.values())}}

    def _handle_gmail_fetch_emails(self, args):
        terms = [t.split(":", 1)[-1].lower() for t in (args.get("query") or "").split() if ":" not in t or t.startswith("from:")]
        def matches(message):
            haystack = " ".join([message["subject"], message["sender"], message["messageText"]]).lower()
            return all(term in haystack for term in terms)
        found = [m for m in self.inbox if matches(m)][: args.get("max_results") or 10]
        return {"successful": True, "data": {"messages": copy.deepcopy(found)}}
