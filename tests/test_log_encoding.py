"""Tests for the one-line log payload encoding shared by all three log stores (P24)."""

import pytest

from server.utils.log_encoding import decode_payload, encode_payload

TRICKY = [
    "",
    "plain",
    "two\nlines",
    "trailing newline\n",
    r"C:\new\notes.txt",
    "real newline then literal: \n" + r"\n",
    "\\",
    "\\\\",
    "ends with backslash\\",
    "backslash before newline \\\nnext",
    r"regex \d+\s*\n and \\n",
    "<tags> & \"quotes\" 'single'",
    "&lt; already-escaped entity",
    "unicode café 日本語 🎉",
]


@pytest.mark.parametrize("text", TRICKY)
def test_round_trip_is_exact(text):
    assert decode_payload(encode_payload(text)) == text


@pytest.mark.parametrize("text", TRICKY)
def test_encoded_payload_is_a_single_line_without_markup(text):
    encoded = encode_payload(text)
    assert "\n" not in encoded and "\r" not in encoded
    assert "<" not in encoded and ">" not in encoded


def test_windows_line_endings_become_newlines():
    assert decode_payload(encode_payload("a\r\nb\rc")) == "a\nb\nc"


@pytest.mark.parametrize(
    "old_line, expected",
    [
        ("line one\\nline two", "line one\nline two"),          # newlines read back as before
        ("a single \\b backslash", "a single \\b backslash"),  # other backslashes are left alone
        ("Tom &amp; Jerry", "Tom & Jerry"),
    ],
)
def test_lines_written_by_the_old_encoder_still_decode(old_line, expected):
    assert decode_payload(old_line) == expected
