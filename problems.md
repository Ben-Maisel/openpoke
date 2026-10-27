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
   - **P26** (+ the env-config half of **P15**): the default model is retired. This blocks every real-LLM run, so it's fixed before the baseline eval.
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
| P1 | Roster stores bare names only, with no description or metadata | A | High | 🟢 Solved |
| P2 | Entire roster is injected into every interaction-agent prompt | A | High | 🟡 In progress |
| P3 | Agents are never retired, so the roster grows forever | A | High | 🔴 Open |
| P4 | Routing relies on the LLM retyping an exact name; near-misses silently create new agents | A | High | 🟢 Solved |
| P5 | Execution agent memory (full log) is loaded into every prompt with no limit | B | High | 🔴 Open |
| P6 | Logged tool data is blindly truncated (args 200 chars, results 500 chars) | B | Medium | 🔴 Open |
| P7 | Every important email and every trigger firing is a separate user ping | C | Medium | 🔴 Open |
| P8 | Server crashes on import on Windows (`fcntl`) | D | High | 🟢 Solved |
| P9 | One global result batch: fast tasks wait for unrelated slow ones | D | High | 🔴 Open |
| P10 | The same execution agent can run twice concurrently | D | Medium | 🔴 Open |
| P11 | Interaction-agent turns from four sources run concurrently and race | D | Medium | 🔴 Open |
| P12 | Failures are silent; the user gets no reply | D | Medium | 🔴 Open |
| P13 | No tests or evaluations exist | E | High | 🟢 Solved |
| P14 | Execution prompt has an unfilled placeholder; agent "purpose" is just its name | E | Low | 🟢 Solved |
| P15 | Claude Sonnet 4 is used for every call, including simple classification | E | Low | 🔴 Open |
| P16 | Drafts aren't rendered as drafts, and "verbatim" relies on the LLM retyping them | E | Low | 🔴 Open |
| P17 | Gmail and search actions are logged under fixed names, not the calling agent | E | Low | 🔴 Open |
| P18 | No push to the browser: the UI re-downloads the full history every 1.5s | E | Low | 🔴 Open |
| P19 | All state is global, so only one user is supported | E | Low | 🔴 Open |
| P20 | "Ask before sending email" is enforced only by the prompt, not by code | D | High | 🔴 Open |
| P21 | Recurring reminders drift an hour after DST in zero-offset zones (UK, Ireland, Portugal) | D | Medium | 🟢 Solved |
| P22 | Unknown reminder statuses (e.g. "cancelled") silently become "active" | D | Medium | 🟢 Solved |
| P23 | A recurring reminder's failure error is erased right after being recorded | D | Low | 🟢 Solved |
| P24 | Logs corrupt any text containing a literal backslash-n (e.g. `C:\new`) | D | Medium | 🟢 Solved |
| P25 | Email bodies come back empty for common formats (e.g. any email with an attachment) | D | High* | 🟢 Solved |
| P26 | The hard-coded default model was retired, so every LLM call fails out of the box | D | Critical | 🟢 Solved |
| P27 | The interaction agent can "edit" a draft itself, so what the user approves isn't what Gmail sends | D | High | 🔴 Open |

---

## A. Orchestrator overload

### P1: Roster stores bare names only
- **Where:** [server/services/execution/roster.py](server/services/execution/roster.py), [server/agents/interaction_agent/agent.py:45](server/agents/interaction_agent/agent.py#L45)
- **What:** `roster.json` is a list of strings. The interaction agent sees `<agent name="..." />` and nothing else: no purpose, people or threads involved, last-used time, pending drafts, or active reminders.
- **Why it matters:** routing decisions are guesses based on a name the LLM itself invented weeks ago.
- **Agreed design (with P2/P4):**
  - **Identity:** every agent gets a stable, short, sequential ID (`a17`), assigned once and never reused. The name is only a label.
  - **Card:** each agent has a card made of a short LLM-written summary of its history, refreshed after each run, plus facts recorded by code from its tool calls (emails, people, threads, drafts, triggers).
  - **Routing (P2/P4):** the interaction agent sees a hot cache of the ~5 most recently used agents plus a shortlist of ~5 search matches over names, cards and facts, all in one prompt, with a `search_agents` tool as a fallback. Creating an agent is an explicit action that checks for likely duplicates first.
- **Progress:**
  - [x] **P1a: the registry.** `server/services/execution/registry.py`: SQLite-backed `AgentRegistry` holding agents (ID, name, summary, status, created / last used, run count) and their facts, validated by kind. IDs come from SQLite `AUTOINCREMENT`, so they are never reused, even after a delete or clear. `tests/test_agent_registry.py`, 27 tests. Not yet used by the app.
  - [x] **P1b: the app runs on IDs.**
    - **Delegation:** `send_message_to_agent` resolves the model's agent name to a registry agent (exact match reused, otherwise created, with the creating instructions as the starting card summary) and dispatches by ID.
    - **Execution:** the execution runtime loads the agent by ID, records each run on its card, and returns both ID and name.
    - **Storage:** agent memory files are keyed by ID (`a1.log`), and triggers are owned by ID. The trigger table's `agent_name` column is renamed to `agent_id` in place for existing databases.
    - **Cleanup:** `roster.py` is removed; "clear history" clears the registry.
    - **Unchanged on purpose:** the interaction agent still sees and uses names until P4. The app-prompt fingerprint is unchanged (`44a60bb84fac425a`), so the committed baselines stay comparable.
    - **Verified:**
      - 589 tests pass
      - P4's memory-collision test now passes
      - new tests cover run counting, ID + name on results, unknown IDs, same-name agents keeping separate memories, and the trigger DB migration
      - a live end-to-end turn on Haiku registered `a1`, ran it, and created a reminder owned by `a1`
  - [x] **P1c: facts recorded by code.** `server/agents/execution_agent/facts.py` holds `extract_facts(tool, args, result)`, a pure function returning facts to add and remove. The execution runtime applies it after every successful tool call, and a failure there never breaks a run.
    - **Rules by tool:**
      - drafts, replies and forwards record recipient emails, display names and threads
      - creating a draft records it as open; sending or deleting it removes it
      - email search records senders, recipients and threads, capped at 20 per kind
      - active triggers are recorded; pausing or completing removes them
      - failed calls, lookups and unknown tools record nothing
    - **Composio's draft-ID field isn't confirmed,** so the common placements are all accepted.
    - **Tests:** `tests/test_agent_facts.py`, 17 tests, including real runs that update the card.
  - [x] **P1d: card summary refreshed after each run.**
    - **What:** `server/agents/execution_agent/card.py`. After every successful run, the execution runtime schedules a background call that rewrites the card from the previous card, the latest request and the agent's report. It's one short call on the summarizer model setting (Haiku), with a strict two-sentence prompt (topic, state, open items).
    - **Doesn't delay anything:** the call runs in the background, so the agent's report is never held up.
    - **Safe on failure:** an empty or failed refresh keeps the old card and logs a warning.
    - **Bounded:** text is tidied and capped at 300 characters. Failed runs don't refresh.
    - **Test support:** the fake LLM gained `default()` responses so card calls don't need scripting in every test.
    - **Tests:** `tests/test_agent_card.py`, 8 tests, including a gated card call proving the report returns before the refresh completes.
    - **Live:** a rent-reminder agent's card refreshed about 1.5 s after its run to "…Active as trigger ID 1, firing 9:00 AM Pacific on the 1st of every month, next on November 1, 2026…".
  - [x] **P1e: legacy import.** `server/services/execution/migration.py` runs at app startup before the trigger scheduler. A pre-registry `roster.json` becomes registered agents:
    - **Card:** each starts from its last request.
    - **Memory:** copied from the slugified-name file to the new ID file. Names that once shared a file each keep that history.
    - **Triggers:** moved from the agent name to the new ID.
    - **Runs once:** the legacy files are renamed `.migrated`, so the import never repeats. An unreadable roster is skipped and left in place.
    - **Tests:** `tests/test_legacy_migration.py`, 9 tests, including the app's startup hook.
- **Solution:** agents now have an identity and a card instead of just a name.
  - **Identity:** a stable ID (`a17`), never reused.
  - **Card:** an LLM-maintained status summary plus exact facts recorded by code (emails, people, threads, open drafts, live triggers) and usage (last used, run count), all in a SQLite registry.
  - **Storage:** memory and triggers are keyed by ID, and existing installs are migrated automatically.
  - **Not yet visible to the interaction agent:** it still sees only names. Showing it cards (the hot cache and shortlist) is P2, and routing by ID is P4.

### P2: Entire roster injected into every prompt
- **Where:** [server/agents/interaction_agent/agent.py:45](server/agents/interaction_agent/agent.py#L45)
- **What:** every agent ever created is listed in every interaction-agent call. The original author flags this as the main known limitation.
- **Why it matters:** token cost and latency grow with every agent. Routing accuracy drops as the model picks from hundreds of similar names.
- **Measured (P13 baseline):** accuracy held on clean rosters, but prompts grow about 19 tokens per agent forever (5.1k → 14.4k at 500 agents). With look-alike duplicates, names alone can't identify the agent holding the context.
- **Progress:**
  - [x] **P2a: search and working set.** `server/services/execution/search.py`.
    - **`search_agents`:** ranks agents by the request's meaningful words found in each agent's name (weight 3), card facts (people, email addresses, IDs; weight 2) and card summary (weight 1). Each word is weighted by how rare it is across agents (IDF), and a quoted agent ID wins outright.
    - **`select_working_set`:** returns the hot cache (5 most recently used) plus the top 5 matches not already shown, so at most 10 agents are listed whatever the roster size.
    - **`AgentRegistry.all_facts()`:** loads every agent's facts in one query.
    - **Tests:** `tests/test_agent_search.py`, 17 tests, including the card telling look-alikes apart and a 500-agent roster yielding a 10-agent working set. Not yet used by the app.
  - [x] **P2b: the prompt shows the working set, not the roster.**
    - **What's in the prompt:** `<active_agents>` now holds `<recent_agents>` (hot cache of 5) and `<matching_agents>` (top 5 search matches). Each agent appears as a card: id, name, last used, summary, open drafts and live reminders. A `<other_agents count="N">` note points to the search tool.
    - **Search input:** the new message plus the last ~600 characters of conversation, so pronoun follow-ups still have something to match.
    - **New `search_agents(query)` tool:** searches every agent and returns up to 5 cards. Its reply format is shared with the eval harness.
    - **System prompt:** explains the groups, says to pick by card rather than by name, and to search before creating.
    - **Eval harness:** builds the same working set with the app's functions, giving roster position as recency, and simulates `search_agents`. It records agents shown and searches made.
    - **Tests:** 670 pass, with new ones for the bounded working set (50 agents → 5 shown + "45 others"), an older matching agent shown with its full card, `search_agents` hits and misses, and the harness equivalents.
    - **Live (Haiku), 31 agents:** "did tom ever get back to me about the lease?" routed straight to the oldest agent, whose card (not its name) mentions Tom.
  - [ ] P2c: give eval roster agents realistic cards, then rerun both evals
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
- **Measured:** with two such duplicates per case, routing accuracy falls from 98–100% to 62–68%, and explicit follow-ups fall to roughly chance (see the messy-roster baseline under P13). Roster size barely matters; duplicates do.
- **Agreed design (with P14):**
  - **Two tools:** `send_message_to_agent(agent_id, …)` for existing agents (an unknown ID is an error, never a silent create), and `create_agent(name, instructions)`.
  - **Duplicate check on create:** it warns with the closest existing agents and the model may confirm with `confirm_new`. Nothing is ever merged automatically.
  - **The model sees** agent IDs and names (cards come with P2's shortlist), and reports carry the ID.
  - **Evals:** both evals are rerun after the change.
- **P4a, the duplicate detector:** `server/services/execution/matching.py`.
  - **How it works:** it compares the *meaningful* words of agent names (generic task words like "email", "reminder", "follow up" and "with" are ignored). Two names count as likely duplicates when they share at least two meaningful words making up at least two-thirds of the shorter name's words, or when both reduce to the same single word.
  - **Coverage:** it catches 42 of the 48 messy-eval decoys.
  - **Known trade-off:** look-alikes differing in one word (Alice Chen vs Alice Wong, Vercel vs Stripe job offer) are flagged too. That's acceptable because the check warns and the model decides.
  - **Tests:** `tests/test_agent_matching.py`, 15 tests.
- **P4b, routing by ID:**
  - **Two tools:** `send_message_to_agent(agent_id, instructions)` returns an error for an unknown ID (with the 3 most recently used agents as hints) and never creates anything. `create_agent(name, instructions, confirm_new?)` runs the duplicate check first; on a likely match it returns `possible_duplicate` with the similar agents' IDs, names and card summaries instead of creating.
  - **IDs everywhere:** the prompt lists `<agent id="a17" name="…" />`, and reports read `[SUCCESS] Lunch with Alice Wong (a17): …`, so IDs flow into the conversation history.
  - **P14 prompt cleanup:** the interaction system prompt explains IDs, `create_agent` and the duplicate reply. The execution prompt's `[TO BE FILLED IN BY USER]` placeholder is gone, and its purpose line is now the agent's card summary.
  - **Eval harness:** it uses the same tools and the same duplicate check and warning text, and counts warnings shown.
  - **Verified:**
    - 646 tests pass, with new tests for create, send-by-ID, unknown IDs, duplicate hold-back, `confirm_new`, and genuinely new tasks
    - live on Haiku: "remind me to call mom…" → `create_agent` (a1); "make the mom call reminder 7pm" → `send_message_to_agent("a1")`, one agent and two runs
- **P4c, evals rerun** (`evals/results/p4`, `evals/results/p4-messy`; Haiku 5.5, 320 decisions, about $0.36):
  - **Clean roster:** accuracy is unchanged (98–100%). Prompt size rose about 20% (5.1k → 6.1k tokens at 10 agents, 14.4k → 17.6k at 500) from IDs in the roster and the longer tool and prompt text. P2's shortlist is what cuts this.
  - **Messy roster:** exact accuracy fell from 62–68% to 57–60%, but **the model reached the right agent far more often (58–71% → 71–96%)**. With IDs, when it can't tell look-alikes apart, it hedges by messaging all of them (1.3 → 1.9 agents per decision). The reports now include a "precision vs reach" table, regenerated for every run from the logged decisions with no new API calls.
  - **Duplicate warnings shown: 0.** The model never tried to create a duplicate in these cases, and no genuinely new task was falsely flagged.
  - **Reading:** P4 fixes the *structural* problem (no silent duplicate creation, no shared memory), but it gives the model no new *information* for telling existing look-alikes apart. That's what cards in the P2 shortlist add. The fan-out is a reasonable hedge for questions but costly, and risky for actions, so P2 should be judged on both precision and reach.
  - **Caveat:** these evals start with duplicates already in the roster, so they can't measure how many duplicates `create_agent` prevents from forming. That benefit shows up over many turns of real use.
- **Solution:** routing by stable ID with an explicit, duplicate-checked `create_agent` (P4a–b, above). A near-miss can no longer silently create an agent or share another's memory.
- **Progress:** the *shared memory* half is fixed by P1b. Memory is keyed by agent ID, so two agents can never share a file, whatever their names (`test_agents_whose_names_collide_never_share_memory`). The *duplicate creation* half (a near-miss name silently creates a new agent) remains, for ID-based routing and the duplicate check.
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
  - Baseline routing eval (before P1):
    - [x] **5a: harness and scoring.**
      - `evals/routing/`:
        - `cases.py`: the case and expectation types
        - `harness.py`: runs one decision on the real model, using the app's own prompt builder; acknowledgement-then-delegate and malformed calls are handled the way the app handles them
        - `scoring.py`: deterministic outcome classification across 6 outcomes plus API errors, near-miss detection, Wilson 95% intervals, and per-group summaries
      - `prepare_message_with_history` gained an optional `agents` argument so the eval can supply a roster. With no argument, behavior is unchanged.
      - The evaluator is itself tested in CI: `tests/test_routing_eval.py`, 32 tests, including a contract test that the history format matches the real conversation log.
      - Live check: 3 cases, all correct, $0.002.
    - [x] **5b: dataset.**
      - `evals/routing/data/world.json`: one fictional user's world, with deliberate name clashes and naming templates modelled on the app's own examples. It generates 949 distractor names.
      - `evals/routing/data/cases.json`: 40 hand-labelled cases, split 10 explicit, 8 implicit (needs history), 6 disambiguation, 10 new task, 6 no delegation.
      - `evals/routing/dataset.py`: builds seeded rosters. They're nested (only noise grows), the correct agent sits at a random but stable position, and each case's `exclude` terms keep competing answers out of the padding.
      - `tests/test_routing_dataset.py`, 133 checks: label consistency, roster fairness at every size, nesting, reproducibility, and position spread.
    - [x] **5c: runner and report.**
      - `python -m evals.routing` logs every decision to `evals/results/<label>/decisions.jsonl` as it completes, with the raw model output, outcome, tokens and cost. It also writes the exact rosters and a `meta.json` identity (model, seed, dataset and app-prompt fingerprints, git commit).
      - Re-running resumes: logged decisions are skipped and only API errors are retried. A different setup is refused.
      - `report.md` is rebuilt from the log with no API calls (`--report-only`).
      - `tests/test_routing_runner.py`, 10 CI tests.
      - Live smoke run: 20 decisions, all correct, $0.015.
    - [x] **Baseline run.** Results are in `evals/results/baseline/`: 160 decisions on Haiku 5.5, $0.17.
      - **Accuracy stays flat:** 98% / 100% / 100% / 98% at 10 / 50 / 200 / 500 agents.
      - **Prompt size grows linearly:** 5,059 → 14,416 tokens per decision, about 19 tokens per agent. Cost per decision rises 2.5×.
      - **Two misses:**
        - a reuse the prompt itself invites (`new-leaking-sink`), with the label deliberately left unchanged after seeing results
        - a draft rewritten by the interaction agent, now tracked as P27
    - [x] **Messy-roster baseline.** Results are in `evals/results/baseline-messy/`: the same cases, plus two realistic duplicate agents per case of the kind P4 creates. $0.18.
      - **Overall accuracy drops to 62–68% at every roster size.**
      - **Implicit follow-ups stay at 100%**, because the history names the real agent.
      - **Explicit follow-ups (10–30%) and disambiguation (0–33%) fall to roughly chance** (1 in 3). With names alone there is no way to tell which duplicate holds the context.
      - **Conclusion:** the damage comes from roster *quality* (duplicates with no distinguishing information), not roster *size*.
- **Solution:** a three-tier quality system.
  - **About 420 deterministic tests run in CI** on Linux and Windows. They use fake LLM and Gmail doubles and cover unit, component and end-to-end flows. Known bugs are pinned as strict expected failures.
  - **A logged, resumable real-model routing eval** (`python -m evals.routing`) has its own CI-tested harness and a fixed, reviewed dataset, and its baseline is committed.
  - **Bugs found along the way:** P21–P25, P27, and P26, which was critical.

### P14: Placeholder prompt
- **Where:** [server/agents/execution_agent/system_prompt.md:27](server/agents/execution_agent/system_prompt.md#L27)
- **What:** the execution agent prompt still contains `[TO BE FILLED IN BY USER]`, and each agent's "purpose" is generated as "Handle tasks related to: {name}".
- **Solution (with P4b):**
  - **Execution prompt:** the placeholder section is removed, and the purpose line is now the agent's card summary ("Your card (what you handle and where things stand): …"), falling back to the old name-based line only for an agent with no summary.
  - **Interaction prompt:** rewritten for ID routing and `create_agent`.

### P15: One expensive model for everything
- **Where:** [server/config.py:54](server/config.py#L54)
- **What:** all five model slots, including the yes/no email importance classifier, use Claude Sonnet 4. Model names are hard-coded rather than read from the environment.
- **Progress:** models are now configurable through the environment, and the default is the much cheaper Haiku 5.5 (see P26). What remains is evaluating whether any slot needs a stronger model, and measuring cost per request.
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
- **Solution:** `build_recurrence` now decides by the zone's *name*. Only zones that are UTC all year (`UTC_ZONE_KEYS`: UTC, Etc/UTC, GMT, Zulu, …) are written as a `…Z` UTC start. Every other zone, including London in winter, gets `DTSTART;TZID=<zone>`, so later occurrences follow its clock changes. Removed the now-unused `timedelta` import.
  - **Verified:**
    - the London DST test passes, with its expected-failure marker removed
    - a new test confirms that London, Lisbon, Dublin and the Canaries keep their zone name
    - a guard test checks that every entry in `UTC_ZONE_KEYS` is a real zone at +0 in both January and July
  - **Not migrated:** reminders already saved with the old logic stay pinned to UTC. There are no real users, so a data fix wasn't worth building.

### P22: Unknown statuses silently become "active"
- **Where:** [server/services/triggers/utils.py:44](server/services/triggers/utils.py#L44)
- **What:** `normalize_status` maps any unrecognized value to `"active"`. An agent cancelling a reminder with `status="cancelled"` (or "deleted", "stopped") leaves it running, and the tool reports success. The only valid way to cancel is `"paused"`, which is documented only in the prompt.
- **Found by:** `test_unrecognised_status_does_not_leave_trigger_active` (expected failure until fixed).
- **Worse than first described:** "active" applied to a *paused* reminder counts as a resume, and if it's overdue it's rescheduled to fire immediately. So "cancel" on a paused, overdue reminder made it fire within seconds. Reproduced before fixing.
- **Solution:** constrain the value at the source *and* validate it in code.
  - **Tool definitions** (`server/agents/execution_agent/tools/triggers.py`): `status` is now an `enum`. `createTrigger` allows `active` / `paused`; `updateTrigger` allows `active` / `paused` / `completed`, and its description says `paused` is how to cancel.
  - **`normalize_status`** (`server/services/triggers/utils.py`): unknown values now raise `ValueError` ("Invalid status 'cancelled'. Use one of: active, completed, paused (use 'paused' to cancel a reminder).") instead of becoming `active`. Missing or blank still means `active`, and surrounding whitespace is trimmed. Both callers validate before writing, so a rejected update changes nothing, and the tool layer passes the error back to the model to retry.
  - **Verified:**
    - the P22 tests pass, with their expected-failure markers removed
    - new tests cover the exact paused-and-overdue scenario (stays paused, not due), rejected creates storing nothing, the error message wording, the enums in both tool definitions, and the tool returning a correctable error
    - live check on Haiku 5.5: asked to "cancel my rent reminder," the agent chose `status: "paused"` on its first attempt

### P23: Recurring reminder errors are erased
- **Where:** [server/services/trigger_scheduler.py](server/services/trigger_scheduler.py) (`_handle_failure`)
- **What:** on failure, the scheduler records `last_error` and then calls `schedule_next_occurrence`, which always clears `last_error`. A recurring reminder can fail on every run and leave no trace.
- **Found by:** `test_failed_recurring_trigger_keeps_error_and_is_rescheduled` (expected failure until fixed).
- **Clarification:** the failure itself was never silent in the moment. Triggers run through the batch manager, which reports `[FAILED] …` to the interaction agent. What was lost was the *record*, so a later check (an agent calling `listTriggers`) showed no error.
- **Solution:** `_handle_failure` now reschedules first and records the error last, so the rescheduling step (which clears `last_error`, correctly, after a success) can no longer erase it.
  - **Verified:**
    - the P23 test passes, with its expected-failure marker removed
    - a later successful run still clears the old error
    - the owning agent sees `last_error` through its `listTriggers` tool
  - **Not changed:** a failed *one-time* reminder keeps `status: "active"` with no next fire time. It's cosmetic and was left alone.

### P24: Logs corrupt literal backslash-n
- **Where:** `_encode_payload` / `_decode_payload`, copy-pasted three times:
  - [server/services/conversation/log.py](server/services/conversation/log.py)
  - [server/services/conversation/summarization/working_memory_log.py](server/services/conversation/summarization/working_memory_log.py)
  - [server/services/execution/log_store.py](server/services/execution/log_store.py)
- **What:** each entry is stored on one line by replacing real newlines with the two characters `\n`. Existing backslashes aren't escaped first, so on read-back *any* literal `\n` in the original text becomes a newline. For example, `C:\new\notes.txt` becomes `C:` + newline + `ew` + newline + `otes.txt`. This silently alters Windows paths, code, and regexes in user messages, emails, and agent memory.
- **Found by:** `test_literal_backslash_n_round_trips` in `tests/test_conversation_log.py` and `tests/test_execution_log_store.py` (expected failures until fixed).
- **Solution:** a single shared encoder, `server/utils/log_encoding.py`, replaces the three copies. It escapes backslashes before newlines (`\` → `\\`, newline → `\n`) and decodes only those two escapes, so any text round-trips exactly. Decoding leaves every other backslash untouched, so lines written by the old encoder still read back as they did before.
  - **Verified:**
    - both P24 tests pass, with their expected-failure markers removed
    - `tests/test_log_encoding.py`: exact round trips across tricky inputs (Windows paths, regexes, trailing backslashes, markup, unicode); every encoded line is a single line with no markup; old-format lines still decode correctly

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
- **Also found while fixing:** a single-part HTML email (no attachments, no plain-text version) was read as plain text with its HTML tags left in, because the plain-text path ignored the part's type.
- **Solution:** body extraction in `server/services/gmail/processing.py` is now a single tree search, `_find_part_text`:
  - **Any depth:** it walks the whole Gmail payload tree and returns the first part of the wanted type (HTML preferred, then plain text).
  - **Missing type:** a part with no type counts as `text/plain`, which is MIME's default.
  - **Charsets:** the part's declared charset is used to decode it.
  - **Padding:** unpadded base64 is padded before decoding.
  - **Bad parts:** an undecodable part is skipped in favor of the next one.
  - **Fallbacks:** `htmlBody`, then `textBody`, then Composio's parsed `messageText`.
  - **Noise filters:** the line-based filters ("Image:", "Alt text:", "View this email in your browser", …) now only match at the start of a line, so real sentences containing those words survive.

  **Verified:**
  - the four P25 tests pass, with their expected-failure markers removed
  - new tests cover single-part HTML, three-level nesting, charsets (including an unknown one), a part with no type, skipping an undecodable part, the `messageText` fallback, and line-start boilerplate still being removed

  **Still open:** a live check against a real Composio response once Gmail is connected.

### P26: Default model no longer exists
- **Where:** [server/config.py](server/config.py) (all five `*_model` settings)
- **What:** every model slot is hard-coded to `anthropic/claude-sonnet-4`. OpenRouter has retired that model and now returns `404: no provider serves this model anymore`. As shipped, the interaction agent, execution agents, search task, summarizer, and email classifier all fail on their first call, so the app does nothing for anyone who clones it today. The model can't be changed without editing code (see P15).
- **Found by:** the first live API call while setting up the P13 baseline eval (2026-10-26).
- **Solution:**
  - **Configurable models.** Each model slot in `server/config.py` is now resolved when `Settings` is built: `OPENPOKE_<SLOT>_MODEL` first, then `OPENPOKE_MODEL` (one setting for every slot), then `DEFAULT_MODEL`. This also solves the "hard-coded" half of P15.
  - **New default.** `anthropic/claude-haiku-5.5` ($0.10 / $0.50 per million input/output tokens) replaces the retired `anthropic/claude-sonnet-4` ($3 / $15). It's a newer generation and roughly 30× cheaper.
  - **Documentation.** The variables are documented in `.env.example`.
  - **Verified:**
    - `tests/test_config.py`, 11 tests: the default, the global override, each per-slot override, blank values, and constructor precedence.
    - A live call through the app's OpenRouter client.
    - A live end-to-end interaction-agent turn, run against a throwaway data directory.

### P27: The interaction agent rewrites drafts itself
- **Where:** interaction-agent behavior, which nothing in [server/agents/interaction_agent/tools.py](server/agents/interaction_agent/tools.py) prevents
- **What:** asked to "make it friendlier and shorter" after a draft was shown, the interaction agent wrote a new version and displayed it with `send_draft` + `send_message_to_user`, without delegating to the agent that owns the Gmail draft. The real draft in Gmail still holds the old text. If the user then says "send it," the drafting agent sends the version the user rejected.
- **Found by:** the P13 baseline routing eval (`implicit-friendlier` at 500 agents). The raw model output is in `evals/results/baseline/decisions.jsonl`.
- **Planned:** resolved by P16 + P20. `send_draft` takes only a `draft_id` and renders stored text, so the interaction agent can't show text that isn't the real draft, and only a shown and approved draft can be sent.
- **Solution:** —

### P19: Single-user global state
- **What:** the conversation log, roster, triggers and Gmail connection are all process-wide singletons with no user ID.
- **Solution:** — (likely out of scope; noted for discussion)
