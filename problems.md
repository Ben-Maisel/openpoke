# Problems Tracker

A running list of problems found in OpenPoke, what causes them, and how each one was resolved.

**How to use this file:** when a problem is fixed, change its status in the summary table, then fill in the **Solution** section of its entry: what changed, which files, and how it was verified (test or eval name).

**Status:** 🔴 Open · 🟡 In progress · 🟢 Solved · ⚪ Won't fix (with reason)

**Areas**
- **A. Orchestrator overload:** the interaction agent is swamped by too many execution agents
- **B. Worker overload:** each execution agent is swamped by its own growing memory
- **C. User overload:** the user is swamped by agent notifications
- **D. Reliability:** concurrency and failure handling
- **E. Quality, cost and operations**

## Work order

Each step builds on the previous ones. Related problems that touch the same code are fixed together.

1. **P8:** Windows crash (nothing runs without this)
2. **P13:** test harness, plus a *baseline* eval of today's routing, so every later fix has a before/after number
   - **P21**, **P22**, **P23**, **P24**, **P25:** bugs found by the P13 tests, fixed as a short batch once the P13 pieces are done
3. **P1:** agent registry with stable IDs and agent cards (foundation for all of area A)
4. **P4** + **P14:** ID-based routing and an explicit "create agent" action; rewrite the prompts that refer to it
5. **P2:** shortlist of relevant agents instead of the full roster
6. **P3:** agent lifecycle (active → dormant → archived)
7. **P6** + **P17:** full-fidelity tool storage, structured digests, correct log attribution
8. **P5:** token-budgeted memory compaction with a facts ledger (also refreshes agent cards)
9. **P10:** one run at a time per agent
10. **P9:** results delivered per request instead of in one global batch
11. **P11:** ordered interaction-agent turns
12. **P12:** failures reach the user
13. **P16** + **P20:** draft store, verbatim drafts by ID, send only approved drafts
14. **P18:** push to the browser over SSE (carries P12's error events)
15. **P7:** notification digest
16. **P15:** model configuration and cheaper models where safe
17. **P19:** discuss only (multi-user is out of scope)

## Summary

| ID | Problem | Area | Severity | Status |
|---|---|---|---|---|
| P1 | Roster stores bare names only, with no description or metadata | A | High | 🔴 Open |
| P2 | Entire roster is injected into every interaction-agent prompt | A | High | 🔴 Open |
| P3 | Agents are never retired, so the roster grows forever | A | High | 🔴 Open |
| P4 | Routing relies on the LLM retyping an exact name; near-misses silently create new agents | A | High | 🔴 Open |
| P5 | Execution agent memory (full log) is loaded into every prompt with no limit | B | High | 🔴 Open |
| P6 | Logged tool data is blindly truncated (args 200 chars, results 500 chars) | B | Medium | 🔴 Open |
| P7 | Every important email and every trigger firing is a separate user ping | C | Medium | 🔴 Open |
| P8 | Server crashes on import on Windows (`fcntl`) | D | High | 🟢 Solved |
| P9 | One global result batch: fast tasks wait for unrelated slow ones | D | High | 🔴 Open |
| P10 | The same execution agent can run twice concurrently | D | Medium | 🔴 Open |
| P11 | Interaction-agent turns from four sources run concurrently and race | D | Medium | 🔴 Open |
| P12 | Failures are silent; the user gets no reply | D | Medium | 🔴 Open |
| P13 | No tests or evaluations exist | E | High | 🟡 In progress |
| P14 | Execution prompt has an unfilled placeholder; agent "purpose" is just its name | E | Low | 🔴 Open |
| P15 | Claude Sonnet 4 is used for every call, including simple classification | E | Low | 🔴 Open |
| P16 | Drafts aren't rendered as drafts, and "verbatim" relies on the LLM retyping them | E | Low | 🔴 Open |
| P17 | Gmail and search actions are logged under fixed names, not the calling agent | E | Low | 🔴 Open |
| P18 | No push to the browser: the UI re-downloads the full history every 1.5s | E | Low | 🔴 Open |
| P19 | All state is global, so only one user is supported | E | Low | 🔴 Open |
| P20 | "Ask before sending email" is enforced only by the prompt, not by code | D | High | 🔴 Open |
| P21 | Recurring reminders drift an hour after DST in zero-offset zones (UK, Ireland, Portugal) | D | Medium | 🔴 Open |
| P22 | Unknown reminder statuses (e.g. "cancelled") silently become "active" | D | Medium | 🔴 Open |
| P23 | A recurring reminder's failure error is erased right after being recorded | D | Low | 🔴 Open |
| P24 | Logs corrupt any text containing a literal backslash-n (e.g. `C:\new`) | D | Medium | 🔴 Open |
| P25 | Email bodies come back empty for common formats (e.g. any email with an attachment) | D | High* | 🔴 Open |

---

## A. Orchestrator overload

### P1: Roster stores bare names only
- **Where:** [server/services/execution/roster.py](server/services/execution/roster.py), [server/agents/interaction_agent/agent.py:45](server/agents/interaction_agent/agent.py#L45)
- **What:** `roster.json` is a list of strings. The interaction agent sees `<agent name="..." />` and nothing else: no purpose, people or threads involved, last-used time, pending drafts, or active reminders.
- **Why it matters:** routing decisions are guesses based on a name the LLM itself invented weeks ago.
- **Solution:** —

### P2: Entire roster injected into every prompt
- **Where:** [server/agents/interaction_agent/agent.py:45](server/agents/interaction_agent/agent.py#L45)
- **What:** every agent ever created is listed in every interaction-agent call. The original author flags this as the main known limitation.
- **Why it matters:** token cost and latency grow with every agent. Routing accuracy drops as the model picks from hundreds of similar names.
- **Solution:** —

### P3: Agents are never retired
- **Where:** [server/services/execution/roster.py](server/services/execution/roster.py)
- **What:** there is no archive or dormant state. The only removal is "clear history," which wipes everything, reminders included.
- **Why it matters:** the roster only grows, which makes P2 worse over time.
- **Solution:** —

### P4: Exact-name routing; near-misses silently create new agents
- **Where:** [server/agents/interaction_agent/tools.py:117](server/agents/interaction_agent/tools.py#L117), [server/services/execution/log_store.py:19](server/services/execution/log_store.py#L19)
- **What:** reuse happens only if the LLM retypes an existing name exactly (case-sensitive). "Lunch with Alice" and "Alice lunch" become two agents, and the new one has no memory. Separately, names that differ only in case or punctuation ("Email to Alice" and "email to alice!") are separate roster entries but slugify to the **same log file**, so they share one memory.
- **Why it matters:** follow-ups such as "send it" can reach an agent that doesn't know the draft. Duplicates also feed P2 and P3.
- **Solution:** —

## B. Worker overload

### P5: Execution agent memory has no limit
- **Where:** [server/agents/execution_agent/agent.py:63](server/agents/execution_agent/agent.py#L63)
- **What:** the agent's entire log is pasted into its system prompt on every run. A `conversation_limit` parameter exists but is never set, and when set it simply drops old requests rather than summarizing them.
- **Why it matters:** long-lived agents get slower and more expensive, and eventually overflow the context window and fail.
- **Solution:** —

### P6: Logged tool data is blindly truncated
- **Where:** [server/agents/execution_agent/agent.py:121-123](server/agents/execution_agent/agent.py#L121-L123)
- **What:** tool arguments are cut at 200 characters and results at 500 before being saved to the agent's memory. A search result containing several full emails keeps roughly the first email's headers, and a draft body is cut after 200 characters.
- **Why it matters:** the reason to reuse an agent is its context, and the context it keeps is clipped arbitrarily.
- **Planned:** never truncate the source of truth. Store full tool outputs on disk under a reference ID. Put a tool-specific structured digest in the prompt (IDs, senders, subjects, dates, snippets), and let the agent re-fetch details by reference. Combine this with the token-budgeted compaction from P5.
- **Solution:** —

## C. User overload

### P7: Notification flood
- **Where:** [server/services/gmail/importance_watcher.py:200-207](server/services/gmail/importance_watcher.py#L200-L207), [server/services/trigger_scheduler.py](server/services/trigger_scheduler.py)
- **What:** each important email triggers its own interaction-agent turn and user message. Each trigger firing also messages the user, even when there's nothing new to say. There is no batching, digest, priority, or quiet hours.
- **Why it matters:** this is the user-facing side of agent overload; more agents mean more interruptions.
- **Solution:** —

## D. Reliability

### P8: Crash on Windows
- **Where:** [server/services/execution/roster.py:4](server/services/execution/roster.py#L4)
- **What:** `import fcntl` is Unix-only, so the server fails to start on Windows.
- **Solution:** removed the `fcntl` lock and its retry loop from `AgentRoster.save()`, which is now a plain JSON write. The lock didn't provide real protection anyway:
  - it truncated the file *before* locking
  - `load()` never locked
  - after five failed attempts it silently dropped the write
  - it guarded against multiple processes, but the server is a single process

  The roster is replaced by the agent registry in P1, so no replacement lock was added. **Verified:** on Windows with Python 3.12, `server.app` imports, the app boots with both background loops, and `/api/v1/health` returns `200`.

### P9: One global result batch
- **Where:** [server/agents/execution_agent/batch_manager.py:44](server/agents/execution_agent/batch_manager.py#L44)
- **What:** all in-flight execution agents share one batch, and results are delivered only when the whole batch drains. A 5-second task waits behind an unrelated 90-second one, and unrelated results get mixed into one report.
- **Also:** batch membership is decided by *timing*, not by which agents were dispatched together. Each agent joins the batch only when its background task starts running, so an agent that finishes before its sibling has started (e.g. an instant failure) is reported separately. Found while building the P13 round-trip tests, whose fake LLM initially answered without yielding.
- **Found by:** `test_fast_result_is_not_held_back_by_an_unrelated_slow_task` in `tests/test_round_trip.py` (strict expected failure).
- **Solution:** —

### P10: The same agent can run concurrently
- **Where:** [server/agents/interaction_agent/tools.py](server/agents/interaction_agent/tools.py)
- **What:** two messages to the same agent start two runs that read the same memory snapshot. Neither sees the other's actions, and their log writes interleave.
- **Solution:** —

### P11: Interaction-agent turns race
- **Where:** [server/agents/interaction_agent/runtime.py](server/agents/interaction_agent/runtime.py)
- **What:** user messages, agent results, trigger output and email alerts each start an interaction-agent turn with no ordering or locking. Turns read stale history and can reply twice. The `wait` tool is largely a workaround for this.
- **Solution:** —

### P12: Silent failures
- **Where:** [server/agents/interaction_agent/runtime.py:186](server/agents/interaction_agent/runtime.py#L186), [server/services/conversation/chat_handler.py:47](server/services/conversation/chat_handler.py#L47)
- **What:** the HTTP request returns `202` immediately. If the interaction agent errors or hits its 8-round limit, nothing is written to the chat, and the UI stops waiting after 30 seconds.
- **Solution:** —

## E. Quality, cost and operations

### P13: No tests or evaluations
- **What:** the repo has no unit tests, integration tests, or evals, so nothing about agent quality is measured.
- **Approach:** three tiers.
  - **Unit tests:** pure code, no LLM; run in CI.
  - **Component tests:** agent loops driven by a scripted fake LLM; run in CI.
  - **Evals:** real model, scored against thresholds; run manually.

  Code that later problems replace is tested as part of those fixes.
- **Progress:**
  - [x] **Scaffolding.**
    - A single `DATA_DIR` in `server/config.py`, overridable with `OPENPOKE_DATA_DIR`, replaces 7 hard-coded copies of the data path.
    - `tests/conftest.py` points it at a throwaway folder and blanks API keys before the server is imported.
    - Added `pytest.ini`, `server/requirements-dev.txt`, and smoke tests in `tests/test_smoke.py` covering the app booting, health, data isolation, and the data-dir override.
  - [x] **CI workflow.** `.github/workflows/tests.yml` runs pytest on every push and pull request across Ubuntu and Windows × Python 3.10 and 3.12 (4 jobs). Real-LLM evals are excluded from CI.
  - Unit tests:
    - [x] **Triggers.** `tests/test_trigger_{utils,service,scheduler}.py`, 62 tests, using a real temporary SQLite file, a frozen clock, and a fake execution agent. These found P21–P23, which are tracked as strict expected failures.
    - [x] **Logs and memory.** `tests/test_conversation_log.py`, `tests/test_summarizer.py` and `tests/test_execution_log_store.py`, 42 tests, with the summarizer LLM faked. These found P24 and confirmed P4's shared-memory collision; both are tracked as strict expected failures.
    - [x] **Email processing.** `tests/test_email_processing.py`, 37 tests on realistic Gmail API payloads. These found P25, tracked as strict expected failures.
    - [x] **Timezones and small stores.** `tests/test_timezones.py` and `tests/test_seen_store.py`, 30 tests covering the timezone store, the timezone helpers, the `/meta/timezone` API route, and the seen-email store. No bugs found.
  - Component tests:
    - [x] **4a: interaction agent.**
      - `tests/fakes.py` provides `ScriptedLLM`, which replaces all 5 LLM call sites, queues scripted responses for each agent type, and records every prompt.
      - `conftest.py` adds the `fake_llm` and `clean_state` fixtures.
      - `tests/test_interaction_agent.py`, 29 tests: replies, the prompt's contents, delegation and reuse, malformed tool calls, the iteration limit, LLM outages, drafts, and the chat HTTP API end to end.
      - Confirms P2 (full roster in every prompt), P12 (failures are silent), and P16 (drafts not marked as drafts).
    - [x] **4b: execution agent, its tools, and the email search task.**
      - `FakeGmail` in `tests/fakes.py` is an in-memory inbox that replaces all 4 Composio call sites and records every action and sent email.
      - New test files, 49 tests in total:
        - `tests/test_execution_agent.py`: the loop, memory loading, tool errors, the iteration limit, outages
        - `tests/test_execution_tools.py`: schema/registry consistency, Gmail tool→action mapping, trigger tools and ownership
        - `tests/test_email_search_task.py`: the search mini-agent, alone and called from an execution agent
      - Confirms P6 (truncated memory), P17 (phantom `gmail-execution-agent` log) and P20 (an unseen draft can be sent) as strict expected failures.
    - [x] **4c: full round trip and concurrency.**
      - `tests/test_round_trip.py`, 7 tests, running the real interaction agent, batch manager, execution agents and tools with only the model and Gmail faked.
      - The headline test runs the draft → confirm → send flow through the same agent.
      - Batching tests cover combined reports, an isolated failure, and a timeout.
      - The fake LLM gained `Gated` responses, which let tests hold an agent "mid-task" without sleeping, and `is_agent(name)` for scripting one specific agent.
      - Confirms P9, P10 and P11 as strict expected failures.
  - [ ] Baseline routing eval (before P1)
- **Solution:** —

### P14: Placeholder prompt
- **Where:** [server/agents/execution_agent/system_prompt.md:27](server/agents/execution_agent/system_prompt.md#L27)
- **What:** the execution agent prompt still contains `[TO BE FILLED IN BY USER]`, and each agent's "purpose" is generated as "Handle tasks related to: {name}".
- **Solution:** —

### P15: One expensive model for everything
- **Where:** [server/config.py:54](server/config.py#L54)
- **What:** all five model slots, including the yes/no email importance classifier, use Claude Sonnet 4. Model names are hard-coded rather than read from the environment.
- **Solution:** —

### P16: Draft handling
- **Where:** [server/agents/interaction_agent/tools.py](server/agents/interaction_agent/tools.py), [server/services/conversation/log.py:175](server/services/conversation/log.py#L175), [web/components/chat/ChatMessages.tsx](web/components/chat/ChatMessages.tsx)
- **What:** the UI has a dedicated "draft" bubble style, but the server records drafts as ordinary assistant replies, so it never triggers. "Verbatim" drafts depend on the interaction agent retyping text from the execution agent's report, rather than pulling the draft by ID.
- **Planned:** when `gmail_create_draft` succeeds, record `{draft_id, agent, to, subject, body}` in a draft store. `send_draft` takes only a `draft_id` and the server renders the stored text, so no LLM retypes it. Record drafts with a `draft` role so the UI's draft bubble works. Closely linked to P20.
- **Solution:** —

### P17: Gmail and search actions logged under the wrong name
- **Where:** [server/agents/execution_agent/tools/gmail.py](server/agents/execution_agent/tools/gmail.py), [server/agents/execution_agent/tasks/search_email/tool.py](server/agents/execution_agent/tasks/search_email/tool.py)
- **What:** Gmail actions are written to a fixed `gmail-execution-agent` log and searches to `task_email_search`. These are phantom logs not tied to the agent that acted.
- **Solution:** —

### P18: Polling instead of push
- **Where:** [web/app/page.tsx](web/app/page.tsx)
- **What:** the browser fetches the **entire** conversation history every 1.5 seconds, and every 1 second while waiting for a reply. Payload size grows with the conversation. The LLM responses aren't streamed either.
- **Planned:** publish an event whenever a message is written to the conversation log (one central write point), and push it to the browser over Server-Sent Events. Each event carries a sequence number so a reconnecting client resumes without gaps. Keep a slow poll only as a fallback. The same channel can carry "working…" and error events, which helps P12.
- **Solution:** —

### P20: Email sending gated only by the prompt
- **Where:** [server/agents/execution_agent/tools/gmail.py](server/agents/execution_agent/tools/gmail.py)
- **What:** "never send without the user's confirmation" exists only as prompt text. In code, `gmail_execute_draft` sends any draft ID the LLM passes, including one the user never saw. Sending email can't be undone.
- **Planned:** using the draft store from P16, only allow a send for a draft that was actually shown to the user, and record the approval.
- **Solution:** —

### P21: Reminders drift after DST in zero-offset timezones
- **Where:** [server/services/triggers/utils.py:104](server/services/triggers/utils.py#L104)
- **What:** `build_recurrence` writes the recurrence start in UTC form whenever the start time's UTC offset *happens to be zero*, when the intent was "the timezone is UTC." A London user creating "daily at 9am" in winter (UTC+0) gets a rule pinned to UTC. After clocks go forward in March, it fires at 10am local. The same applies to Ireland and Portugal.
- **Found by:** `test_recurring_trigger_in_zero_offset_zone_keeps_local_time_across_dst_start` (marked expected failure until fixed).
- **Solution:** —

### P22: Unknown statuses silently become "active"
- **Where:** [server/services/triggers/utils.py:44](server/services/triggers/utils.py#L44)
- **What:** `normalize_status` maps any unrecognized value to `"active"`. An agent cancelling a reminder with `status="cancelled"` (or "deleted", "stopped") leaves it running, and the tool reports success. The only valid way to cancel is `"paused"`, which is documented only in the prompt.
- **Found by:** `test_unrecognised_status_does_not_leave_trigger_active` (expected failure until fixed).
- **Solution:** —

### P23: Recurring reminder errors are erased
- **Where:** [server/services/trigger_scheduler.py](server/services/trigger_scheduler.py) (`_handle_failure`)
- **What:** on failure, the scheduler records `last_error` and then calls `schedule_next_occurrence`, which always clears `last_error`. A recurring reminder can fail on every run and leave no trace.
- **Found by:** `test_failed_recurring_trigger_keeps_error_and_is_rescheduled` (expected failure until fixed).
- **Solution:** —

### P24: Logs corrupt literal backslash-n
- **Where:** `_encode_payload` / `_decode_payload`, copy-pasted three times:
  - [server/services/conversation/log.py](server/services/conversation/log.py)
  - [server/services/conversation/summarization/working_memory_log.py](server/services/conversation/summarization/working_memory_log.py)
  - [server/services/execution/log_store.py](server/services/execution/log_store.py)
- **What:** each entry is stored on one line by replacing real newlines with the two characters `\n`. Existing backslashes aren't escaped first, so on read-back *any* literal `\n` in the original text becomes a newline. For example, `C:\new\notes.txt` becomes `C:` + newline + `ew` + newline + `otes.txt`. This silently alters Windows paths, code, and regexes in user messages, emails, and agent memory.
- **Found by:** `test_literal_backslash_n_round_trips` in `tests/test_conversation_log.py` and `tests/test_execution_log_store.py` (expected failures until fixed).
- **Solution:** —

### P25: Email body extraction misses common formats
- **Where:** [server/services/gmail/processing.py:191-224](server/services/gmail/processing.py#L191-L224) (`_extract_html_body`, `_extract_plain_body`, `post_process_text`)
- **What:**
  - **Nested parts are ignored.** The body search looks only one level into the Gmail `payload` tree. Gmail's usual shape for an email with an attachment nests the readable body one level deeper (`multipart/mixed` → `multipart/alternative` → `text/html`), so the cleaned body comes back **empty**.
  - **Plain-text parts are never read.** The plain-text path checks only the top-level body. A `text/plain` part inside a multipart email is skipped, again giving an empty body.
  - **Unpadded base64 is dropped.** Base64 without `=` padding raises on decode, and the body is silently discarded.
  - **Legitimate text is deleted.** The noise filters remove real content, e.g. "Your Docker image: nginx:1.25 was pushed" loses everything after "image:".
  - **An available fallback is ignored.** The email search task asks Composio for `verbose` output specifically to get a parsed `messageText` field, but processing never reads `messageText`. It looks for `htmlBody`/`textBody` instead.
- **Why it matters:** an empty body reaches both the execution agents (search results) and the important-email classifier. An email with an attachment can't be summarized or triaged.
- **\*Severity caveat:** confirmed against the standard Gmail API payload shape. How often it bites depends on exactly what Composio returns, which will be verified with a live fetch once Gmail is connected.
- **Found by:** four strict expected failures in `tests/test_email_processing.py`.
- **Solution:** —

### P19: Single-user global state
- **What:** the conversation log, roster, triggers and Gmail connection are all process-wide singletons with no user ID.
- **Solution:** — (likely out of scope; noted for discussion)
