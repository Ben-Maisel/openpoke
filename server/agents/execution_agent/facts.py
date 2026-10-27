"""Derive card facts (emails, people, threads, open drafts, live triggers) from an agent's tool calls.

Pure functions: given one successful tool call, say which facts to add to and remove
from the agent's card. Recording them in code keeps them exact and free, so routing
can match "did Alice Wong reply?" against alice.wong@... no matter how a summary is worded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NAMED_ADDRESS = re.compile(r"([^<>,;\"]+?)\s*<\s*([\w.+-]+@[\w-]+(?:\.[\w-]+)+)\s*>")
_MAX_FACTS_PER_KIND = 20  # a broad search shouldn't flood the card


@dataclass
class FactChanges:
    add: Dict[str, Set[str]] = field(default_factory=dict)
    remove: Dict[str, Set[str]] = field(default_factory=dict)

    def added(self, kind: str, values: Iterable[Optional[str]]) -> None:
        cleaned = {v.strip() for v in values if isinstance(v, str) and v.strip()}
        if cleaned:
            self.add.setdefault(kind, set()).update(cleaned)

    def removed(self, kind: str, values: Iterable[Optional[str]]) -> None:
        cleaned = {v.strip() for v in values if isinstance(v, str) and v.strip()}
        if cleaned:
            self.remove.setdefault(kind, set()).update(cleaned)

    def capped(self) -> "FactChanges":
        self.add = {kind: set(sorted(values)[:_MAX_FACTS_PER_KIND]) for kind, values in self.add.items()}
        return self


# Facts implied by one successful tool call (unknown tools and errors imply none)
def extract_facts(tool_name: str, arguments: Dict[str, Any], result: Any) -> FactChanges:
    changes = FactChanges()
    if isinstance(result, dict) and (result.get("error") or result.get("successful") is False):
        return changes
    args = arguments or {}

    if tool_name in ("gmail_create_draft", "gmail_reply_to_thread"):
        _add_addresses(changes, [args.get("recipient_email"), *_as_list(args.get("cc")),
                                 *_as_list(args.get("bcc")), *_as_list(args.get("extra_recipients"))])
        changes.added("thread", [args.get("thread_id")])
        if tool_name == "gmail_create_draft":
            changes.added("draft", [_draft_id(result)])
    elif tool_name == "gmail_forward_email":
        _add_addresses(changes, [args.get("recipient_email")])
    elif tool_name in ("gmail_execute_draft", "gmail_delete_draft"):
        changes.removed("draft", [args.get("draft_id")])  # sent or deleted: no longer open
    elif tool_name == "task_email_search" and isinstance(result, list):
        for email in result:
            if isinstance(email, dict):
                _add_addresses(changes, [email.get("sender"), email.get("recipient")])
                changes.added("thread", [email.get("thread_id")])
    elif tool_name == "createTrigger" and isinstance(result, dict):
        if result.get("status", "active") == "active":
            changes.added("trigger", [_str(result.get("trigger_id"))])
    elif tool_name == "updateTrigger" and isinstance(result, dict):
        trigger = _str(result.get("trigger_id"))
        if result.get("status") == "active":
            changes.added("trigger", [trigger])
        elif result.get("status") in ("paused", "completed"):
            changes.removed("trigger", [trigger])
    return changes.capped()


def _add_addresses(changes: FactChanges, fields: Iterable[Any]) -> None:
    for text in fields:
        if not isinstance(text, str):
            continue
        changes.added("person", [name.strip().strip("'") for name, _ in _NAMED_ADDRESS.findall(text)])
        changes.added("email", [address.lower() for address in _EMAIL.findall(text)])


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _draft_id(result: Any) -> Optional[str]:
    """Composio's exact response shape isn't confirmed, so accept the common placements."""
    if not isinstance(result, dict):
        return None
    for container in (result.get("data"), result):
        if isinstance(container, dict):
            for key in ("id", "draft_id", "draftId"):
                if container.get(key):
                    return _str(container[key])
    return None


def _str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = ["FactChanges", "extract_facts"]
