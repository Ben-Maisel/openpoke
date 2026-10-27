# Routing eval: `baseline-messy`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `44a60bb84fac425a`
- **Roster variant:** messy (two duplicate decoy agents per case; see `evals/routing/data/decoys.json`)
- **Code:** `06bab63261`
- **Decisions:** 160 · **total cost:** $0.1784

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **62%** | 47%–76% | `████████████░░░░░░░░` | 5,294 | $0.00074 | 40 | 0 |
| 50 | **68%** | 52%–80% | `██████████████░░░░░░` | 5,824 | $0.00080 | 40 | 0 |
| 200 | **68%** | 52%–80% | `██████████████░░░░░░` | 9,485 | $0.00116 | 40 | 0 |
| 500 | **68%** | 52%–80% | `██████████████░░░░░░` | 15,386 | $0.00175 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 14 | 0 | 1 | 0 | 0 | 0 | 0 |
| 50 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| 200 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| 500 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 17% | 0% | 33% | 17% |
| explicit followup | 10% | 30% | 10% | 20% |
| implicit followup | 100% | 100% | 100% | 100% |
| new task | 90% | 100% | 100% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (54)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `disambig-alice-wong` | Lunch with Alice Wong | Alice Wong Lunch, Friday Lunch with Alice | wrong agent |
| 10 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 10 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing | wrong agent |
| 10 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation | wrong agent |
| 10 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 10 | `explicit-board-deck` | Board Deck Review with Hannah | Board Deck Review with Hannah, Board Deck Review | wrong agent |
| 10 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Claim Follow-up | wrong agent |
| 10 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 10 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Landlord Lease Renewal, Lease Renewal Follow-up | wrong agent |
| 10 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Email Marcus and Grace | wrong agent |
| 10 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section | wrong agent |
| 10 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge | wrong agent |
| 10 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking | wrong agent |
| 10 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Follow-up, Vercel Offer Negotiation | wrong agent |
| 10 | `new-leaking-sink` | NEW | Rent Payment Reminder | wrongly reused |
| 50 | `disambig-alice-wong` | Lunch with Alice Wong | Alice Wong Lunch, Friday Lunch with Alice | wrong agent |
| 50 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 50 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing | wrong agent |
| 50 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation | wrong agent |
| 50 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 50 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft | wrong agent |
| 50 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review, Board Deck Review with Hannah | wrong agent |
| 50 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 50 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Landlord Lease Renewal, Lease Renewal Follow-up | wrong agent |
| 50 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Email Marcus and Grace | wrong agent |
| 50 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section | wrong agent |
| 50 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking | wrong agent |
| 50 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Negotiation, Vercel Offer Follow-up, Vercel Job Offer | wrong agent |
| 200 | `disambig-alice-wong` | Lunch with Alice Wong | Alice Wong Lunch | wrong agent |
| 200 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing | wrong agent |
| 200 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation | wrong agent |
| 200 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 200 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review, Board Deck Review with Hannah | wrong agent |
| 200 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Car Insurance Claim, Car Insurance Claim | wrong agent |
| 200 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 200 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Lease Renewal Follow-up | wrong agent |
| 200 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Email Marcus and Grace | wrong agent |
| 200 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section | wrong agent |
| 200 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge | wrong agent |
| 200 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking, Tokyo Trip Planning | wrong agent |
| 200 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Job Offer, Vercel Offer Negotiation, Vercel Offer Follow-up | wrong agent |
| 500 | `disambig-alice-wong` | Lunch with Alice Wong | Alice Wong Lunch | wrong agent |
| 500 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing | wrong agent |
| 500 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation | wrong agent |
| 500 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 500 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft | wrong agent |
| 500 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review | wrong agent |
| 500 | `explicit-car-claim` | Geico Car Insurance Claim | Car Insurance Claim | wrong agent |
| 500 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 500 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Landlord Lease Renewal, Lease Renewal Follow-up | wrong agent |
| 500 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section | wrong agent |
| 500 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge | wrong agent |
| 500 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking | wrong agent |
| 500 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Negotiation | wrong agent |
