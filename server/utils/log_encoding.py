"""Encode text so each log entry fits on one line, and decode it back exactly."""

from __future__ import annotations

import re
from html import escape, unescape

# Only these two escapes are produced. Decoding ignores any other backslash, so lines
# written by the old encoder (which didn't escape backslashes) still read back unchanged.
_ESCAPE = re.compile(r"\\([\\n])")


def encode_payload(payload: str) -> str:
    normalized = payload.replace("\r\n", "\n").replace("\r", "\n")
    collapsed = normalized.replace("\\", "\\\\").replace("\n", "\\n")
    return escape(collapsed, quote=False)


def decode_payload(payload: str) -> str:
    return _ESCAPE.sub(lambda m: "\n" if m.group(1) == "n" else "\\", unescape(payload))


__all__ = ["decode_payload", "encode_payload"]
