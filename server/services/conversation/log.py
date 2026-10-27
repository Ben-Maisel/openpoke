from __future__ import annotations

import re
import threading
from html import escape
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Protocol, Tuple

from ...config import DATA_DIR, get_settings
from ...logging_config import logger
from ...models import ChatMessage
from ...utils.log_encoding import decode_payload as _decode_payload, encode_payload as _encode_payload
from ...utils.timezones import now_in_user_timezone
from .events import get_conversation_events
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - used for type checkers only
    from .summarization import WorkingMemoryLog


_CONVERSATION_LOG_PATH = DATA_DIR / "conversation" / "poke_conversation.log"


class TranscriptFormatter(Protocol):
    def __call__(self, tag: str, timestamp: str, payload: str) -> str:  # pragma: no cover - typing protocol
        ...



def _default_formatter(tag: str, timestamp: str, payload: str) -> str:
    encoded = _encode_payload(payload)
    return f"<{tag} timestamp=\"{timestamp}\">{encoded}</{tag}>\n"


def _resolve_working_memory_log() -> "WorkingMemoryLog":
    from .summarization import get_working_memory_log

    return get_working_memory_log()


_ATTR_PATTERN = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")

# Log entries the user sees, and the chat role each is shown with
_CHAT_ROLES = {"user_message": "user", "poke_reply": "assistant", "draft": "draft"}


class ConversationLog:
    """Append-only conversation log persisted to disk for the interaction agent."""

    def __init__(self, path: Path, formatter: TranscriptFormatter = _default_formatter):
        self._path = path
        self._formatter = formatter
        self._lock = threading.Lock()
        self._count: Optional[int] = None  # entries in the file; an entry's sequence number is its position
        self._ensure_directory()
        self._working_memory_log = _resolve_working_memory_log()

    def _ensure_directory(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("conversation log directory creation failed", extra={"error": str(exc)})

    def _append(self, tag: str, payload: str) -> str:
        timestamp = now_in_user_timezone("%Y-%m-%d %H:%M:%S")
        entry = self._formatter(tag, timestamp, str(payload))
        with self._lock:
            seq = self._count_locked() + 1
            try:
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(entry)
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "conversation log append failed",
                    extra={"error": str(exc), "tag": tag, "path": str(self._path)},
                )
                raise
            self._count = seq
        if tag in _CHAT_ROLES:  # push it to open browsers (P18)
            get_conversation_events().publish(
                {"type": "message", "seq": seq, "role": _CHAT_ROLES[tag], "content": str(payload), "timestamp": timestamp}
            )
        self._notify_summarization()
        return timestamp

    def entry_count(self) -> int:
        """The sequence number of the latest entry (0 when the log is empty)."""
        with self._lock:
            return self._count_locked()

    def _count_locked(self) -> int:
        """How many entries the file holds; read once, then kept up to date. The caller holds the lock."""
        if self._count is None:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            self._count = sum(1 for line in lines if self._parse_line(line) is not None)
        return self._count

    def _parse_line(self, line: str) -> Optional[Tuple[str, str, str]]:
        stripped = line.strip()
        if not stripped.startswith("<") or "</" not in stripped:
            return None
        open_end = stripped.find(">")
        if open_end == -1:
            return None
        open_tag_content = stripped[1:open_end]
        if " " in open_tag_content:
            tag, attr_string = open_tag_content.split(" ", 1)
        else:
            tag, attr_string = open_tag_content, ""
        close_start = stripped.rfind("</")
        close_end = stripped.rfind(">")
        if close_start == -1 or close_end == -1:
            return None
        closing_tag = stripped[close_start + 2 : close_end]
        if closing_tag != tag:
            return None
        payload = stripped[open_end + 1 : close_start]
        attributes: Dict[str, str] = {
            match.group(1): match.group(2) for match in _ATTR_PATTERN.finditer(attr_string)
        }
        timestamp = attributes.get("timestamp", "")
        return tag, timestamp, _decode_payload(payload)

    def iter_entries(self) -> Iterator[Tuple[str, str, str]]:
        with self._lock:
            try:
                lines = self._path.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "conversation log read failed", extra={"error": str(exc), "path": str(self._path)}
                )
                raise
        for line in lines:
            item = self._parse_line(line)
            if item is not None:
                yield item

    def load_transcript(self) -> str:
        parts: List[str] = []
        for tag, timestamp, payload in self.iter_entries():
            safe_payload = escape(payload, quote=False)
            if timestamp:
                parts.append(f"<{tag} timestamp=\"{timestamp}\">{safe_payload}</{tag}>")
            else:
                parts.append(f"<{tag}>{safe_payload}</{tag}>")
        return "\n".join(parts)

    def record_user_message(self, content: str) -> None:
        timestamp = self._append("user_message", content)
        self._working_memory_log.append_entry("user_message", content, timestamp)

    def record_agent_message(self, content: str) -> None:
        timestamp = self._append("agent_message", content)
        self._working_memory_log.append_entry("agent_message", content, timestamp)

    def record_reply(self, content: str) -> None:
        timestamp = self._append("poke_reply", content)
        self._working_memory_log.append_entry("poke_reply", content, timestamp)

    def record_draft(self, content: str) -> None:
        """Record an email draft exactly as the user is shown it (rendered by the UI as a draft bubble)."""
        timestamp = self._append("draft", content)
        self._working_memory_log.append_entry("draft", content, timestamp)

    def record_wait(self, reason: str) -> None:
        """Record a wait marker that should not reach the user-facing chat history."""
        timestamp = self._append("wait", reason)
        self._working_memory_log.append_entry("wait", reason, timestamp)

    def _notify_summarization(self) -> None:
        settings = get_settings()
        if not settings.summarization_enabled:
            return

        try:
            from .summarization import schedule_summarization  # type: ignore import-not-found
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "summarization scheduler unavailable",
                extra={"error": str(exc)},
            )
            return

        try:
            schedule_summarization()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "failed to schedule summarization",
                extra={"error": str(exc)},
            )

    def to_chat_messages(self, after: int = 0) -> List[ChatMessage]:
        """What the user sees, each with its sequence number; `after` skips messages the client already has."""
        messages: List[ChatMessage] = []
        for seq, (tag, timestamp, payload) in enumerate(self.iter_entries(), start=1):
            # Agent reports and wait markers are orchestration metadata and never surface to the user
            if seq > after and tag in _CHAT_ROLES:
                messages.append(
                    ChatMessage(role=_CHAT_ROLES[tag], content=payload, timestamp=timestamp or None, seq=seq)
                )
        return messages

    def clear(self) -> None:
        with self._lock:
            try:
                if self._path.exists():
                    self._path.unlink()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "conversation log clear failed", extra={"error": str(exc), "path": str(self._path)}
                )
            finally:
                self._ensure_directory()
                self._count = 0
        get_conversation_events().publish({"type": "cleared"})
        try:
            self._working_memory_log.clear()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "working memory clear skipped",
                extra={"error": str(exc)},
            )


_conversation_log = ConversationLog(_CONVERSATION_LOG_PATH)


def get_conversation_log() -> ConversationLog:
    return _conversation_log


__all__ = ["ConversationLog", "get_conversation_log"]
