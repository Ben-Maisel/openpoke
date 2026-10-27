# Memory eval: `memory-v2`

One long-lived worker answers questions about its own past runs. **before** is the original app (whole journal in the prompt, tool data cut to 200/500 characters, no recall); **after** is the current app (card, facts, recent runs within the history budget, and the recall tool). Live tools are unavailable in both, so answers come from memory.

- **Model:** `anthropic/claude-haiku-5.5` · **card model:** `anthropic/claude-haiku-5.5` · **seed** 20261027 · **history budget:** 4000 tokens
- **Fingerprints:** dataset `49f54a9fdce4d50a` · app `2a178a3e00e2c83f` · legacy `856e56aa75575344`
- **Code:** `2a3e8fae74` (uncommitted changes)
- **Answers:** 60 · **API errors:** 0 · **total cost:** $0.2050

## Accuracy and prompt size by journal length

Prompt tokens are for the worker's first call, i.e. the memory it starts from (95% CI in brackets).

| Runs in journal | after accuracy | after prompt tokens | after: answers using recall |
|---:|---:|---:|---:|
| 20 | **100%** (84%–100%) | 10,973 | 55% |
| 60 | **90%** (70%–97%) | 12,366 | 60% |
| 200 | **100%** (84%–100%) | 12,348 | 75% |

## Accuracy by question category

| Category | after @ 20 | after @ 60 | after @ 200 |
|---|---:|---:|---:|
| changed | 100% | 75% | 100% |
| old detail | 100% | 86% | 100% |
| open item | 100% | 100% | 100% |
| preference | 100% | 100% | 100% |
| recent detail | 100% | 100% | 100% |

## The card the real refresh produced (after the last run)

- **20 runs:** Denver offsite trip for Acme (Nov 12-15): booked UA 1482 SFO-DEN Nov 12 and Hyatt Regency Denver Nov 12-15 (rate unverified vs $250 cap); Lyft airport ride set for Nov 12 morning, pickup time unconfirmed. Open: return flight not found, check-in reminder 37 unconfirmed, no packing emails, weekend options unbooked, Jordan's roadmap review due EOD Tuesday, expense draft d-417 to Dana awaiting send approval, Priya Shah dinner Fri Nov 13 7:30 PM at Tavernetta (Priya replied; draft d-301 deleted). Prefs: aisle seats, hotels under $250/night, pack layers for cold evenings, 16th Street food hall for s
- **60 runs:** Denver offsite Nov 12-15 planning: Hyatt Regency Denver replaces cancelled Marriott; UA 1482 SFO-DEN booked Nov 12, return still unbooked (reminder 37); Lyft airport pickup set for Nov 12 morning. Awaiting approval: d-417 expense summary to Dana Ort; the Nov 13 Tavernetta 7:30 PM dinner with Priya Shah is settled since she replied and draft d-301 was deleted. Open: Jordan's roadmap slides due EOD Tue Nov 11 (five max), convention-center lunch (16th Street food hall idea), carpool emails, outings, packing list. Settled: aisle seats, hotels under $250/night, cc Dana Ort; Acme checks 41, 46, 49 c
- **200 runs:** Denver offsite trip (Nov 12-15) planning. Booked: UA 1482 out Nov 12, Acme seats 76-77 aisle, Lyft airport ride Nov 12 (pickup time unconfirmed), Priya Shah dinner Nov 13 at 7:30 (she replied; draft d-301 deleted), Dana Ortiz on expenses, day-two team photo. Draft d-417 (expense summary to Dana) awaits approval to send. Open: Jordan's roadmap slides due EOD Tuesday, unsent airport carpool email, no packing list, reminder 37 vs 24 hours pending, Hyatt vs Marriott unverified, weekend options and 16th Street food hall lunch undecided.

## Every miss (2)

| Runs | Condition | Question | Expected | Answer | Recalls |
|---:|---|---|---|---|---:|
| 60 | after | `hotel-confirmation` | HY-5528 | Hotel confirmation number: not found. I couldn't retrieve it because the email search tool returned "Service unavailable" on all three attempts. What I tried: 1… | 0 |
| 60 | after | `offsite-room` | Larimer B | I couldn't find the room. The email search returned "Service unavailable" on both attempts, so I couldn't search the inbox directly. The only offsite email I co… | 1 |
