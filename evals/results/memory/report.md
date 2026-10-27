# Memory eval: `memory`

One long-lived worker answers questions about its own past runs. **before** is the original app (whole journal in the prompt, tool data cut to 200/500 characters, no recall); **after** is the current app (card, facts, recent runs within the history budget, and the recall tool). Live tools are unavailable in both, so answers come from memory.

- **Model:** `anthropic/claude-haiku-5.5` · **card model:** `anthropic/claude-haiku-5.5` · **seed** 20261027 · **history budget:** 4000 tokens
- **Fingerprints:** dataset `49f54a9fdce4d50a` · app `91d38aabde19f3b1` · legacy `856e56aa75575344`
- **Code:** `fd6630a46a`
- **Answers:** 120 · **API errors:** 0 · **total cost:** $0.7783

## Accuracy and prompt size by journal length

Prompt tokens are for the worker's first call, i.e. the memory it starts from (95% CI in brackets).

| Runs in journal | before accuracy | after accuracy | before prompt tokens | after prompt tokens | after: answers using recall |
|---:|---:|---:|---:|---:|---:|
| 20 | **60%** (39%–78%) | **100%** (84%–100%) | 11,716 | 10,973 | 55% |
| 60 | **60%** (39%–78%) | **95%** (76%–99%) | 25,051 | 12,366 | 60% |
| 200 | **60%** (39%–78%) | **80%** (58%–92%) | 71,744 | 12,348 | 75% |

## Accuracy by question category

| Category | before @ 20 | after @ 20 | before @ 60 | after @ 60 | before @ 200 | after @ 200 |
|---|---:|---:|---:|---:|---:|---:|
| changed | 25% | 100% | 25% | 100% | 25% | 75% |
| old detail | 43% | 100% | 43% | 86% | 43% | 71% |
| open item | 100% | 100% | 100% | 100% | 100% | 100% |
| preference | 100% | 100% | 100% | 100% | 100% | 67% |
| recent detail | 75% | 100% | 75% | 100% | 75% | 100% |

## The card the real refresh produced (after the last run)

- **20 runs:** Denver offsite trip for Acme (Nov 12-15): booked UA 1482 SFO-DEN Nov 12 and Hyatt Regency Denver Nov 12-15 (rate unverified vs $250 cap); Lyft airport ride set for Nov 12 morning, pickup time unconfirmed. Open: return flight not found, check-in reminder 37 unconfirmed, no packing emails, weekend options unbooked, Jordan's roadmap review due EOD Tuesday, expense draft d-417 to Dana awaiting send approval, Priya Shah dinner Fri Nov 13 7:30 PM at Tavernetta (Priya replied; draft d-301 deleted). Prefs: aisle seats, hotels under $250/night, pack layers for cold evenings, 16th Street food hall for s
- **60 runs:** Denver offsite Nov 12-15 planning: Hyatt Regency Denver replaces cancelled Marriott; UA 1482 SFO-DEN booked Nov 12, return still unbooked (reminder 37); Lyft airport pickup set for Nov 12 morning. Awaiting approval: d-417 expense summary to Dana Ort; the Nov 13 Tavernetta 7:30 PM dinner with Priya Shah is settled since she replied and draft d-301 was deleted. Open: Jordan's roadmap slides due EOD Tue Nov 11 (five max), convention-center lunch (16th Street food hall idea), carpool emails, outings, packing list. Settled: aisle seats, hotels under $250/night, cc Dana Ort; Acme checks 41, 46, 49 c
- **200 runs:** Denver offsite trip (Nov 12-15) planning. Booked: UA 1482 out Nov 12, Acme seats 76-77 aisle, Lyft airport ride Nov 12 (pickup time unconfirmed), Priya Shah dinner Nov 13 at 7:30 (she replied; draft d-301 deleted), Dana Ortiz on expenses, day-two team photo. Draft d-417 (expense summary to Dana) awaits approval to send. Open: Jordan's roadmap slides due EOD Tuesday, unsent airport carpool email, no packing list, reminder 37 vs 24 hours pending, Hyatt vs Marriott unverified, weekend options and 16th Street food hall lunch undecided.

## Every miss (29)

| Runs | Condition | Question | Expected | Answer | Recalls |
|---:|---|---|---|---|---:|
| 20 | before | `expense-total` | 1,184.60 / 1184.60 | The drafts service returned "Service unavailable" on two attempts, so I can't read draft d-417's body from here. The execution history also cuts off the draft b… | 0 |
| 20 | before | `flight-arrival` | 10:38 | Poke, I could not find the landing time for your flight. The email search service returned "Service unavailable" on all three attempts. What I know from earlier… | 0 |
| 20 | before | `flight-confirmation` | QX7R2P | I couldn't find the confirmation code. The email search service returned "Service unavailable" on all three attempts. - The United booking email is message ID 1… | 0 |
| 20 | before | `flight-seat` | 14C | I couldn't find your seat assignment. The email search service returned "Service unavailable" on all three attempts. - **Flight:** UA 1482, SFO to DEN, Thursday… | 0 |
| 20 | before | `hotel-address` | 650 15th | I couldn't find the hotel address. The email search service returned "Service unavailable" on all three attempts, using these queries: "Hyatt Regency Denver res… | 0 |
| 20 | before | `hotel-confirmation` | HY-5528 | I couldn't find your hotel confirmation number. The email search service returned "Service unavailable" on all three attempts, so I have no results to check. Tw… | 0 |
| 20 | before | `hotel-rate` | 239 | I couldn't find the nightly rate because the email search service is returning "Service unavailable." I tried three searches, and the last retry also failed. Th… | 0 |
| 20 | before | `offsite-room` | Larimer B | I couldn't find the room for the offsite. All three searches (Acme Denver offsite room, location/venue, and room assignment) returned "Service unavailable," so … | 0 |
| 60 | after | `offsite-room` | Larimer B | I couldn't find the offsite room. The email search failed twice with "Service unavailable," and my history has no room or venue information. What I do have: - T… | 1 |
| 60 | before | `expense-total` | 1,184.60 / 1184.60 | I couldn't determine the total of the expense report draft. Gmail's draft lookup failed twice with "Service unavailable," and my execution history cuts off the … | 0 |
| 60 | before | `flight-arrival` | 10:38 | I couldn't find the arrival time. The email search tool returned "Service unavailable" on all three attempts, so I have no flight arrival data to report. What I… | 0 |
| 60 | before | `flight-confirmation` | QX7R2P | I couldn't find the confirmation code. The email search failed three times in a row with "Service unavailable," so I have no results to report. The United booki… | 0 |
| 60 | before | `flight-seat` | 14C | I couldn't find your seat assignment. The email search failed three times in a row with "Service unavailable," using three different queries about UA 1482 and s… | 0 |
| 60 | before | `hotel-address` | 650 15th | I couldn't find your current hotel's street address. The email search service returned "Service unavailable" on all three attempts, so I could not check for a H… | 0 |
| 60 | before | `hotel-confirmation` | HY-5528 | I couldn't find your hotel confirmation number. The email search service returned "Service unavailable" on all three attempts. Two things Poke should know befor… | 0 |
| 60 | before | `hotel-rate` | 239 | I couldn't find the nightly rate. The email search service returned "Service unavailable" on all three attempts, so I have no confirmed rate. Here is what the r… | 0 |
| 60 | before | `offsite-room` | Larimer B | I couldn't find the room location. The email search returned "Service unavailable" on all three attempts, so I have no result to report, and I did not guess. Tw… | 0 |
| 200 | after | `flight-arrival` | 10:38 | I couldn't find your flight's landing time. The email search has failed twice with "Service unavailable," and my past records don't include an arrival time. Wha… | 1 |
| 200 | after | `hotel-address` | 650 15th | I couldn't find the street address. The email search returned "Service unavailable" on both attempts, and the address isn't in my saved notes. What I do have: t… | 2 |
| 200 | after | `hotel-budget` | 250 | I couldn't find a nightly hotel budget limit. The email search returned "Service unavailable" on two attempts, and my past run history has no record of one. Wha… | 1 |
| 200 | after | `offsite-room` | Larimer B | I couldn't find which room the offsite is in. - My email searches for the offsite room and venue returned "Service unavailable" (three attempts, with different … | 2 |
| 200 | before | `expense-total` | 1,184.60 / 1184.60 | I couldn't retrieve the total. The draft lookup failed twice with "Service unavailable," so I couldn't open draft d-417. The execution history shows that draft … | 0 |
| 200 | before | `flight-arrival` | 10:38 | Poke, I couldn't find the landing time for your flight. The email search service returned "Service unavailable" on all three attempts. What I know so far: - The… | 0 |
| 200 | before | `flight-confirmation` | QX7R2P | Final output for Poke: I could not find the airline confirmation code for the Denver flight. The email search service returned "Service unavailable" on three at… | 0 |
| 200 | before | `flight-seat` | 14C | I couldn't find the seat assignment. The email search failed three times in a row with "Service unavailable," so I have no seat information yet. The United itin… | 0 |
| 200 | before | `hotel-address` | 650 15th | I couldn't find the hotel address. The email search failed three times in a row with "Service unavailable." Two things to know before retrying: - **The Hyatt bo… | 0 |
| 200 | before | `hotel-confirmation` | HY-5528 | I couldn't find your hotel confirmation number. The search service returned "Service unavailable" on three attempts, two with different queries and one retry. P… | 0 |
| 200 | before | `hotel-rate` | 239 | I couldn't find the nightly rate. The email search service returned "Service unavailable" on all three attempts, so I have no current data to report. What the r… | 0 |
| 200 | before | `offsite-room` | Larimer B | The offsite room location is unknown. Both searches for the Acme offsite room failed with "Service unavailable," and one retry also failed. The emails I've seen… | 0 |
