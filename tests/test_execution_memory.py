"""Tests for how an execution agent's journal is shown in its prompt (digests; no LLM)."""

import json

import pytest

from server.agents.execution_agent.memory import GENERIC_CHARS, MAX_LISTED, SNIPPET_CHARS, digest_tool_call, render_history


def call(tool, arguments=None, result=None, ok=True, ref="r7"):
    return {"ref": ref, "tool": tool, "ok": ok, "arguments": arguments or {}, "result": result}


def email(i, text="See you at noon."):
    return {"id": f"m-{i}", "thread_id": f"t-{i}", "timestamp": "2026-10-20 14:05:00+00:00",
            "sender": "Alice <alice@example.com>", "recipient": "me@example.com",
            "subject": f"Lunch {i}", "clean_text": text}


# --- Per-tool digests -------------------------------------------------------------

def test_search_digest_lists_every_email_with_its_ids():
    text = digest_tool_call(call("task_email_search", {"search_query": "lunch"}, [email(1), email(2)]))
    assert text.splitlines() == [
        'task_email_search [r7]: searched "lunch" → 2 email(s)',
        "- m-1 (thread t-1) | 2026-10-20 14:05 | from Alice <alice@example.com> | to me@example.com | Lunch 1 | See you at noon.",
        "- m-2 (thread t-2) | 2026-10-20 14:05 | from Alice <alice@example.com> | to me@example.com | Lunch 2 | See you at noon.",
    ]


def test_search_snippets_are_one_short_line():
    long_text = "word\n\n" * 200
    line = digest_tool_call(call("task_email_search", {"search_query": "q"}, [email(1, long_text)])).splitlines()[1]
    snippet = line.rsplit(" | ", 1)[1]
    assert "\n" not in snippet
    assert len(snippet) == SNIPPET_CHARS + 1 and snippet.endswith("…")


def test_large_search_points_to_the_full_record_for_the_rest():
    text = digest_tool_call(call("task_email_search", {"search_query": "q"}, [email(i) for i in range(MAX_LISTED + 3)]))
    lines = text.splitlines()
    assert len(lines) == 1 + MAX_LISTED + 1
    assert lines[-1] == "- … 3 more (full result: r7)"


def test_draft_digest_keeps_the_whole_body():
    body = "Hi Alice,\n\n" + "Plenty of detail. " * 100 + "\nTHE ENDING."
    args = {"recipient_email": "alice@example.com", "cc": ["bob@example.com"], "subject": "Lunch", "body": body}
    text = digest_tool_call(call("gmail_create_draft", args, {"successful": True, "data": {"id": "d-9"}}))
    assert text == f"gmail_create_draft [r7]: draft d-9 to alice@example.com (cc bob@example.com) | subject: Lunch\n{body}"


def test_reply_and_forward_digests():
    reply = digest_tool_call(call("gmail_reply_to_thread",
                                  {"thread_id": "t-1", "recipient_email": "a@x.com", "message_body": "Sounds good!"}))
    assert reply == "gmail_reply_to_thread [r7]: replied in thread t-1 to a@x.com\nSounds good!"
    forward = digest_tool_call(call("gmail_forward_email",
                                    {"message_id": "m-1", "recipient_email": "b@x.com", "additional_text": "FYI"}))
    assert forward == "gmail_forward_email [r7]: forwarded email m-1 to b@x.com\nFYI"


@pytest.mark.parametrize("tool, verb", [("gmail_execute_draft", "sent"), ("gmail_delete_draft", "deleted")])
def test_send_and_delete_draft_digests(tool, verb):
    assert digest_tool_call(call(tool, {"draft_id": "d-9"})) == f"{tool} [r7]: {verb} draft d-9"


def test_trigger_digests_show_id_status_schedule_and_text():
    result = {"trigger_id": 4, "status": "active", "next_trigger": "2026-11-01T09:00:00-07:00",
              "recurrence_rule": "RRULE:FREQ=DAILY"}
    text = digest_tool_call(call("createTrigger", {"payload": "Take vitamins"}, result))
    assert text == ('createTrigger [r7]: trigger 4 active, next 2026-11-01T09:00:00-07:00, '
                    'repeats RRULE:FREQ=DAILY | "Take vitamins"')
    paused = digest_tool_call(call("updateTrigger", {"trigger_id": 4, "status": "paused"},
                                   {**result, "status": "paused", "recurrence_rule": None}))
    assert paused == "updateTrigger [r7]: trigger 4 paused, next 2026-11-01T09:00:00-07:00, repeats never"


def test_list_triggers_digest():
    triggers = [{"id": 1, "status": "active", "next_trigger": "2026-11-01", "recurrence_rule": None, "payload": "Dentist"}]
    assert digest_tool_call(call("listTriggers", {}, {"triggers": triggers})).splitlines() == [
        "listTriggers [r7]: 1 trigger(s)",
        '- trigger 1 active, next 2026-11-01, repeats never | "Dentist"',
    ]


def test_other_tools_get_compact_json_and_a_pointer_when_cut():
    small = digest_tool_call(call("gmail_get_contacts", {"page_size": 5}, {"contacts": ["a"]}))
    assert small == 'gmail_get_contacts [r7]: args {"page_size":5} → {"contacts":["a"]}'
    big = digest_tool_call(call("gmail_list_drafts", {}, {"drafts": ["x" * 50] * 50}))
    result_part = big.split(" → ", 1)[1]
    assert result_part.endswith("… (full result: r7)")
    assert len(result_part) == GENERIC_CHARS + len("… (full result: r7)")


def test_failed_calls_show_the_error_and_arguments():
    text = digest_tool_call(call("gmail_execute_draft", {"draft_id": "d-1"}, {"error": "Draft not found"}, ok=False))
    assert text == 'gmail_execute_draft [r7] failed: Draft not found | args {"draft_id":"d-1"}'
    as_text = digest_tool_call(call("createTrigger", {}, "Invalid status 'cancelled'", ok=False))
    assert as_text.startswith("createTrigger [r7] failed: Invalid status 'cancelled'")


def test_odd_result_shapes_never_crash_a_digest():
    assert "0 email(s)" in digest_tool_call(call("task_email_search", {}, {"unexpected": True}))
    assert digest_tool_call(call("createTrigger", {}, "weird")).startswith('createTrigger [r7]: args {} → "weird"')
    assert digest_tool_call(call("listTriggers", {}, None)) == "listTriggers [r7]: 0 trigger(s)"
    assert "draft None" in digest_tool_call(call("gmail_create_draft", {}, None))
    assert digest_tool_call({"tool": "x", "ok": True, "arguments": "not a dict"}) == "x [?]: args {} → null"


# --- Rendering the journal -------------------------------------------------------

def test_history_shows_digests_for_tool_calls_and_text_for_everything_else():
    record = call("gmail_execute_draft", {"draft_id": "d-1"})
    entries = [
        ("agent_request", "2026-10-27 09:00:00", "Send the <lunch> draft"),
        ("tool_call", "2026-10-27 09:00:01", json.dumps(record)),
        ("agent_response", "2026-10-27 09:00:02", "Sent & done"),
    ]
    assert render_history(entries).splitlines() == [
        '<agent_request timestamp="2026-10-27 09:00:00">Send the &lt;lunch&gt; draft</agent_request>',
        '<tool_call timestamp="2026-10-27 09:00:01">gmail_execute_draft [r7]: sent draft d-1</tool_call>',
        '<agent_response timestamp="2026-10-27 09:00:02">Sent &amp; done</agent_response>',
    ]


def test_journals_written_before_digests_still_render():
    entries = [
        ("agent_action", "t", "Calling gmail_create_draft with: {...}"),
        ("tool_response", "t", 'gmail_create_draft: {"id": "d-1"}'),
        ("tool_call", "", "not json"),
    ]
    assert render_history(entries).splitlines() == [
        '<agent_action timestamp="t">Calling gmail_create_draft with: {...}</agent_action>',
        '<tool_response timestamp="t">gmail_create_draft: {"id": "d-1"}</tool_response>',
        "<tool_call>not json</tool_call>",
    ]


def test_empty_history_renders_nothing():
    assert render_history([]) == ""


# --- Budgeted history (P5) ---------------------------------------------------------

from server.agents.execution_agent.memory import CHARS_PER_TOKEN, render_facts, render_recent_history, split_runs  # noqa: E402


def run(i, size=0):
    return [("agent_request", "t", f"request {i}"), ("agent_response", "t", f"done {i}" + "x" * size)]


def journal(n, size=0):
    return [entry for i in range(n) for entry in run(i, size)]


def test_runs_end_at_each_response_and_trailing_entries_are_in_progress():
    trigger_run = [("tool_call", "t", "{}"), ("agent_response", "t", "reminded")]  # fired by a trigger: no request
    entries = run(0) + trigger_run + [("agent_request", "t", "current")]
    runs, in_progress = split_runs(entries)
    assert runs == [run(0), trigger_run]
    assert in_progress == [("agent_request", "t", "current")]


def test_short_history_is_shown_whole_without_a_marker():
    text = render_recent_history(journal(3), token_budget=4000)
    assert "earlier_runs" not in text
    assert [f"request {i}" in text for i in range(3)] == [True] * 3


def test_budget_keeps_the_newest_whole_runs_and_counts_the_rest():
    # Each run renders to roughly 1,100 characters, so a 1,000-token (4,000-character) budget fits three.
    text = render_recent_history(journal(10, size=1000), token_budget=1000)
    lines = text.splitlines()
    assert lines[0] == '<earlier_runs count="7">Not shown. Your card above summarizes them; use recall to search them.</earlier_runs>'
    assert [line.split(">")[1].split("<")[0] for line in lines[1::2]] == ["request 7", "request 8", "request 9"]
    assert all(line.startswith("<agent_response") for line in lines[2::2])  # no run is cut in half


def test_the_latest_run_is_always_shown_even_when_it_alone_exceeds_the_budget():
    text = render_recent_history(journal(3, size=10_000), token_budget=100)
    assert text.splitlines()[0].startswith('<earlier_runs count="2">')
    assert "request 2" in text and "request 1" not in text


def test_the_run_in_progress_is_always_shown():
    entries = journal(5, size=5_000) + [("agent_request", "t", "the current request")]
    text = render_recent_history(entries, token_budget=100)
    assert text.endswith('<agent_request timestamp="t">the current request</agent_request>')
    assert "request 4" in text


def test_prompt_history_stays_flat_however_long_the_journal_grows():
    budget = 4000
    texts = {n: render_recent_history(journal(n, size=300), budget) for n in (20, 200, 2000)}
    assert texts[200].count("<agent_request") == texts[2000].count("<agent_request") < 200
    assert all(len(text) <= budget * CHARS_PER_TOKEN + 100 for text in texts.values())


def test_facts_render_as_labelled_lines_in_a_fixed_order():
    facts = {"email": ["alice@x.com"], "draft": ["r-1", "r-2"], "trigger": ["4"], "thread": [], "person": ["Alice"]}
    assert render_facts(facts).splitlines() == [
        "- Open drafts: r-1, r-2",
        "- Live reminders: 4",
        "- People: Alice",
        "- Email addresses: alice@x.com",
    ]


def test_no_facts_says_so():
    assert render_facts({}) == "None recorded yet."
    assert render_facts({"draft": []}) == "None recorded yet."


def test_long_fact_lists_are_capped():
    line = render_facts({"email": [f"p{i:03}@x.com" for i in range(MAX_LISTED + 5)]})
    assert line.endswith("p019@x.com (+5 more)")
    assert line.count("@") == MAX_LISTED


def test_recall_digests_say_what_was_looked_up_not_the_whole_result():
    assert digest_tool_call(call("recall", {"ref": "r3"}, {"record": "x" * 9000})) == "recall [r7]: looked up r3 in full"
    searched = digest_tool_call(call("recall", {"query": "hotel"}, {"matches": [{}, {}]}))
    assert searched == 'recall [r7]: searched history for "hotel" → 2 match(es)'
