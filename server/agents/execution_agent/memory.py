"""Render an execution agent's journal for its prompt.

The journal on disk keeps every tool call in full. The prompt shows a digest of each one:
the IDs, people, subjects and text the agent needs to pick up where it left off, with
anything cut short pointing back to the full record by its reference (e.g. r3).

The prompt only holds the most recent runs that fit a token budget; older runs are
covered by the agent's card summary and its facts, which are rendered separately.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from ...services.execution.log_store import TOOL_CALL_TAG
from .facts import draft_id

SNIPPET_CHARS = 150  # per email in a search digest
GENERIC_CHARS = 400  # for tools without a dedicated digest
MAX_LISTED = 20  # emails or triggers listed before "… N more"
CHARS_PER_TOKEN = 4  # rough estimate; avoids a tokenizer dependency

Entry = Tuple[str, str, str]


def _compact(value: Any, limit: int, ref: str) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= limit else f"{text[:limit]}… (full result: {ref})"


def _one_line(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def _search(args: Dict[str, Any], result: Any, ref: str) -> str:
    emails = [e for e in result if isinstance(e, dict)] if isinstance(result, list) else []
    lines = [f"searched \"{args.get('search_query', '')}\" → {len(emails)} email(s)"]
    for email in emails[:MAX_LISTED]:
        lines.append(
            f"- {email.get('id')} (thread {email.get('thread_id')}) | {str(email.get('timestamp', ''))[:16]}"
            f" | from {email.get('sender')} | to {email.get('recipient')} | {email.get('subject')}"
            f" | {_one_line(email.get('clean_text'), SNIPPET_CHARS)}"
        )
    if len(emails) > MAX_LISTED:
        lines.append(f"- … {len(emails) - MAX_LISTED} more (full result: {ref})")
    return "\n".join(lines)


def _recipients(args: Dict[str, Any]) -> str:
    text = str(args.get("recipient_email"))
    for field in ("cc", "bcc"):
        if args.get(field):
            text += f" ({field} {', '.join(map(str, args[field]))})"
    return text


def _create_draft(args: Dict[str, Any], result: Any, ref: str) -> str:
    return (f"draft {draft_id(result)} to {_recipients(args)} | subject: {args.get('subject')}\n"
            f"{args.get('body', '')}")


def _reply(args: Dict[str, Any], result: Any, ref: str) -> str:
    return f"replied in thread {args.get('thread_id')} to {_recipients(args)}\n{args.get('message_body', '')}"


def _forward(args: Dict[str, Any], result: Any, ref: str) -> str:
    note = f"\n{args['additional_text']}" if args.get("additional_text") else ""
    return f"forward {draft_id(result)} of email {args.get('message_id')} to {args.get('recipient_email')}{note}"


def _trigger_line(trigger: Dict[str, Any], payload: Any = None) -> str:
    line = (f"trigger {trigger.get('trigger_id', trigger.get('id'))} {trigger.get('status')}"
            f", next {trigger.get('next_trigger')}, repeats {trigger.get('recurrence_rule') or 'never'}")
    payload = payload or trigger.get("payload")
    return f"{line} | \"{_one_line(payload, SNIPPET_CHARS)}\"" if payload else line


def _trigger(args: Dict[str, Any], result: Any, ref: str) -> str:
    if not isinstance(result, dict):
        return _generic(args, result, ref)
    return _trigger_line(result, args.get("payload"))


def _list_triggers(args: Dict[str, Any], result: Any, ref: str) -> str:
    triggers = result.get("triggers", []) if isinstance(result, dict) else []
    lines = [f"{len(triggers)} trigger(s)"] + [f"- {_trigger_line(t)}" for t in triggers[:MAX_LISTED]]
    if len(triggers) > MAX_LISTED:
        lines.append(f"- … {len(triggers) - MAX_LISTED} more (full result: {ref})")
    return "\n".join(lines)


def _recall(args: Dict[str, Any], result: Any, ref: str) -> str:
    result = result if isinstance(result, dict) else {}
    if args.get("ref"):
        return f"looked up {args['ref']} in full"
    return f"searched history for \"{args.get('query', '')}\" → {len(result.get('matches') or [])} match(es)"


def _generic(args: Dict[str, Any], result: Any, ref: str) -> str:
    return f"args {_compact(args, GENERIC_CHARS, ref)} → {_compact(result, GENERIC_CHARS, ref)}"


_DIGESTS: Dict[str, Callable[[Dict[str, Any], Any, str], str]] = {
    "task_email_search": _search,
    "gmail_create_draft": _create_draft,
    "gmail_reply_to_thread": _reply,
    "gmail_forward_email": _forward,
    "gmail_execute_draft": lambda args, result, ref: f"sent draft {args.get('draft_id')}",
    "gmail_delete_draft": lambda args, result, ref: f"deleted draft {args.get('draft_id')}",
    "createTrigger": _trigger,
    "updateTrigger": _trigger,
    "listTriggers": _list_triggers,
    "recall": _recall,
}


# One tool call as the agent sees it in its history
def digest_tool_call(record: Dict[str, Any]) -> str:
    ref, tool = record.get("ref", "?"), record.get("tool", "?")
    args = record.get("arguments") if isinstance(record.get("arguments"), dict) else {}
    result = record.get("result")
    if not record.get("ok"):
        error = result.get("error", result) if isinstance(result, dict) else result
        return f"{tool} [{ref}] failed: {_one_line(error, GENERIC_CHARS)} | args {_compact(args, GENERIC_CHARS, ref)}"
    return f"{tool} [{ref}]: {_DIGESTS.get(tool, _generic)(args, result, ref)}"


def _render_payload(tag: str, payload: str) -> str:
    if tag != TOOL_CALL_TAG:
        return payload  # requests, responses, and pre-digest journal lines are shown as written
    try:
        return digest_tool_call(json.loads(payload))
    except (ValueError, AttributeError):
        return payload


# Journal entries (tag, timestamp, payload) as tagged prompt text
def render_history(entries: Iterable[Entry]) -> str:
    parts: List[str] = []
    for tag, timestamp, payload in entries:
        text = escape(_render_payload(tag, payload), quote=False)
        parts.append(f"<{tag} timestamp=\"{timestamp}\">{text}</{tag}>" if timestamp else f"<{tag}>{text}</{tag}>")
    return "\n".join(parts)


# Split a journal into runs; a run ends with the agent's response, and anything after the last one is in progress
def split_runs(entries: Sequence[Entry]) -> Tuple[List[List[Entry]], List[Entry]]:
    runs: List[List[Entry]] = []
    current: List[Entry] = []
    for entry in entries:
        current.append(entry)
        if entry[0] == "agent_response":
            runs.append(current)
            current = []
    return runs, current


# The newest whole runs that fit the budget (the latest one always), plus a note about the rest
def render_recent_history(entries: Sequence[Entry], token_budget: int) -> str:
    runs, in_progress = split_runs(entries)
    budget = token_budget * CHARS_PER_TOKEN - len(render_history(in_progress))
    kept: List[str] = []
    for run in reversed(runs):
        text = render_history(run)
        if kept and len(text) > budget:
            break
        kept.insert(0, text)
        budget -= len(text)
    omitted = len(runs) - len(kept)
    parts = [f'<earlier_runs count="{omitted}">Not shown. Your card above summarizes them; use recall to search them.</earlier_runs>'] if omitted else []
    parts += kept
    if in_progress:
        parts.append(render_history(in_progress))
    return "\n".join(parts)


_FACT_LABELS = (("draft", "Open drafts"), ("trigger", "Live reminders"), ("thread", "Threads"),
                ("person", "People"), ("email", "Email addresses"))


# The agent's recorded facts as short labelled lines (each kind capped so the list stays bounded)
def render_facts(facts: Mapping[str, Sequence[str]]) -> str:
    lines = []
    for kind, label in _FACT_LABELS:
        values = list(facts.get(kind) or [])
        if values:
            more = f" (+{len(values) - MAX_LISTED} more)" if len(values) > MAX_LISTED else ""
            lines.append(f"- {label}: {', '.join(values[:MAX_LISTED])}{more}")
    return "\n".join(lines) or "None recorded yet."


__all__ = ["digest_tool_call", "render_facts", "render_history", "render_recent_history", "split_runs"]
