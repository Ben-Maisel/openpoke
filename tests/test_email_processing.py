"""Tests for turning raw Gmail payloads into clean ProcessedEmail objects (no network)."""

import base64
from datetime import datetime, timedelta, timezone

import pytest

from server.services.gmail.processing import (
    EmailTextCleaner,
    build_processed_email,
    parse_gmail_fetch_response,
    parse_gmail_timestamp,
)

UTC = timezone.utc


def b64(text: str, *, encoding: str = "utf-8", pad: bool = True) -> str:
    encoded = base64.urlsafe_b64encode(text.encode(encoding)).decode("ascii")
    return encoded if pad else encoded.rstrip("=")


def part(mime_type: str, text: str = "", *, parts=None, filename: str = "", **b64_kwargs) -> dict:
    """Build one node of a Gmail API `payload` tree."""
    node = {"mimeType": mime_type, "filename": filename, "body": {"data": b64(text, **b64_kwargs)} if text else {}}
    if parts is not None:
        node["parts"] = parts
    return node


@pytest.fixture
def cleaner():
    return EmailTextCleaner(max_url_length=60)


# --- HTML cleaning --------------------------------------------------------

def test_html_cleaning_keeps_content_and_drops_markup_noise(cleaner):
    html = """
    <html><head><title>Ignore</title><style>p{color:red}</style></head>
    <body><script>track()</script><img src="x.png">
    <p>Lunch is at <b>noon</b> on Friday.</p>
    <div style="display:none">hidden preheader</div>
    <footer>Company Inc, 1 Main St</footer></body></html>
    """
    text = cleaner.clean_html_email(html)
    assert "Lunch is at" in text and "noon" in text and "on Friday." in text
    for noise in ("Ignore", "color:red", "track()", "hidden preheader", "Company Inc"):
        assert noise not in text


def test_links_keep_their_text_and_destination(cleaner):
    text = cleaner.clean_html_email('<p><a href="https://example.com/rsvp">RSVP here</a></p>')
    assert text == "RSVP here (https://example.com/rsvp)"


def test_bare_url_links_are_marked_as_links(cleaner):
    text = cleaner.clean_html_email('<a href="https://example.com">https://example.com</a>')
    assert text == "[Link: https://example.com]"


def test_tracking_params_are_removed_before_truncating(cleaner):
    url = "https://shop.example.com/order/123?utm_source=email&utm_medium=x&utm_campaign=fall&id=9"
    assert cleaner.truncate_url(url) == "https://shop.example.com/order/123?id=9"


def test_urls_still_too_long_are_truncated(cleaner):
    url = "https://example.com/" + "a" * 100
    assert cleaner.truncate_url(url) == url[:60] + "..."


def test_entities_and_whitespace_are_normalised(cleaner):
    text = cleaner.post_process_text("Tom &amp; Jerry\n\n\n\n\nsee   you\t\tthere")
    assert text == "Tom & Jerry\n\nsee you there"


def test_boilerplate_lines_are_removed(cleaner):
    text = cleaner.post_process_text(
        "View this email in your browser\nReal content\nPlease do not reply to this email.\n"
    )
    assert text == "Real content"


@pytest.mark.xfail(strict=True, reason="P25: noise filters delete legitimate text containing 'image:'")
def test_legitimate_text_resembling_noise_is_kept(cleaner):
    text = cleaner.post_process_text("Your Docker image: nginx:1.25 was pushed\nDeploy finished")
    assert "nginx:1.25" in text


def test_fallback_extraction_strips_tags(cleaner):
    assert cleaner.fallback_text_extraction("<p>Hello <b>there</b></p>") == "Hello there"


# --- Finding the body inside a Gmail payload ------------------------------

def test_html_part_is_preferred_over_plain_text(cleaner):
    message = {"payload": part("multipart/alternative", parts=[
        part("text/plain", "plain version"),
        part("text/html", "<p>html version</p>"),
    ])}
    assert cleaner.clean_email_content(message) == "html version"


def test_single_part_plain_text_email(cleaner):
    message = {"payload": part("text/plain", "Just text, no HTML.")}
    assert cleaner.clean_email_content(message) == "Just text, no HTML."


def test_falls_back_to_flat_body_fields(cleaner):
    assert cleaner.clean_email_content({"textBody": "flat text"}) == "flat text"
    assert cleaner.clean_email_content({"htmlBody": "<p>flat html</p>"}) == "flat html"


def test_message_without_body_is_empty(cleaner):
    assert cleaner.clean_email_content({"payload": {}}) == ""


@pytest.mark.xfail(strict=True, reason="P25: body search ignores nested multipart (any email with an attachment)")
def test_body_is_found_in_email_with_attachment(cleaner):
    # Gmail's usual shape for "email + attachment": the readable body is
    # nested one level down, inside multipart/alternative.
    message = {"payload": part("multipart/mixed", parts=[
        part("multipart/alternative", parts=[
            part("text/plain", "See the attached agenda."),
            part("text/html", "<p>See the attached agenda.</p>"),
        ]),
        part("application/pdf", filename="agenda.pdf"),
    ])}
    assert cleaner.clean_email_content(message) == "See the attached agenda."


@pytest.mark.xfail(strict=True, reason="P25: plain-text parts inside multipart are never read")
def test_plain_text_part_inside_multipart_is_found(cleaner):
    message = {"payload": part("multipart/mixed", parts=[
        part("text/plain", "Plain body only."),
        part("application/pdf", filename="scan.pdf"),
    ])}
    assert cleaner.clean_email_content(message) == "Plain body only."


@pytest.mark.xfail(strict=True, reason="P25: base64 without '=' padding fails to decode and the body is dropped")
def test_unpadded_base64_body_is_decoded(cleaner):
    message = {"payload": part("text/plain", "Hello", pad=False)}
    assert cleaner.clean_email_content(message) == "Hello"


# --- Attachments ----------------------------------------------------------

def test_attachment_info_collects_named_files(cleaner):
    attachments = [{"filename": "agenda.pdf"}, {"name": "photo.jpg"}, {"filename": ""}, "junk", None]
    assert cleaner.extract_attachment_info(attachments) == (True, 2, ["agenda.pdf", "photo.jpg"])


def test_attachment_info_handles_none(cleaner):
    assert cleaner.extract_attachment_info(None) == (False, 0, [])


# --- Timestamps -----------------------------------------------------------

def test_parse_timestamp_handles_z_suffix():
    assert parse_gmail_timestamp("2026-10-26T12:00:00Z") == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_parse_timestamp_treats_naive_as_utc():
    assert parse_gmail_timestamp("2026-10-26T12:00:00") == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("raw", [None, "", "yesterday", "26/10/2026"])
def test_parse_timestamp_rejects_bad_input(raw):
    assert parse_gmail_timestamp(raw) is None


# --- build_processed_email ------------------------------------------------

FULL_MESSAGE = {
    "messageId": "m-1",
    "threadId": "t-1",
    "messageTimestamp": "2026-10-26T12:00:00Z",
    "subject": "Lunch Friday?",
    "sender": "Alice <alice@example.com>",
    "to": "me@example.com",
    "labelIds": ["INBOX", "UNREAD"],
    "payload": part("text/plain", "Are you free Friday?"),
    "attachmentList": [{"filename": "menu.pdf"}],
}


def test_builds_processed_email_from_full_message():
    email = build_processed_email(FULL_MESSAGE, query="from:alice")
    assert (email.id, email.thread_id, email.query) == ("m-1", "t-1", "from:alice")
    assert (email.subject, email.sender, email.recipient) == ("Lunch Friday?", "Alice <alice@example.com>", "me@example.com")
    assert email.timestamp == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    assert email.label_ids == ["INBOX", "UNREAD"]
    assert email.clean_text == "Are you free Friday?"
    assert (email.has_attachments, email.attachment_count, email.attachment_filenames) == (True, 1, ["menu.pdf"])


def test_message_without_id_is_skipped():
    assert build_processed_email({"subject": "no id"}, query="q") is None
    assert build_processed_email({"messageId": "   "}, query="q") is None


def test_id_falls_back_to_id_field_and_is_trimmed():
    assert build_processed_email({"id": "  m-2  "}, query="q").id == "m-2"


def test_missing_fields_get_safe_defaults():
    email = build_processed_email({"messageId": "m-3"}, query="q")
    assert (email.subject, email.sender, email.recipient) == ("No Subject", "Unknown Sender", "Unknown Recipient")
    assert email.thread_id is None
    assert email.clean_text == ""
    assert abs(email.timestamp - datetime.now(UTC)) < timedelta(minutes=1)


# --- parse_gmail_fetch_response -------------------------------------------

def test_parses_data_wrapped_response_with_next_page():
    raw = {"data": {"messages": [FULL_MESSAGE, {"messageId": "m-2"}], "nextPageToken": "page-2"}}
    emails, next_page = parse_gmail_fetch_response(raw, query="q")
    assert [e.id for e in emails] == ["m-1", "m-2"]
    assert next_page == "page-2"


def test_parses_top_level_messages_and_lists_of_containers():
    raw = [
        {"messages": [{"messageId": "a"}]},
        {"data": {"messages": [{"messageId": "b"}], "nextPageToken": "first"}},
        {"data": {"messages": [{"messageId": "c"}], "nextPageToken": "second"}},
    ]
    emails, next_page = parse_gmail_fetch_response(raw, query="q")
    assert [e.id for e in emails] == ["a", "b", "c"]
    assert next_page == "first"


def test_messages_without_ids_are_dropped_from_response():
    raw = {"data": {"messages": [{"subject": "no id"}, "junk", {"messageId": "ok"}]}}
    emails, _ = parse_gmail_fetch_response(raw, query="q")
    assert [e.id for e in emails] == ["ok"]


@pytest.mark.parametrize("raw", [None, "error string", 42, [1, "x"], {"data": "oops"}, {"data": {"messages": None}}])
def test_malformed_responses_yield_nothing(raw):
    assert parse_gmail_fetch_response(raw, query="q") == ([], None)
