# Routing eval: `p4-messy`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `68e83b09628f4de3`
- **Roster variant:** messy (two duplicate decoy agents per case; see `evals/routing/data/decoys.json`)
- **Code:** `09cb2b743d` (uncommitted changes)
- **Decisions:** 160 · **total cost:** $0.2041 · **duplicate warnings shown:** 0

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **57%** | 42%–71% | `████████████░░░░░░░░` | 6,112 | $0.00082 | 40 | 0 |
| 50 | **60%** | 45%–74% | `████████████░░░░░░░░` | 7,034 | $0.00093 | 40 | 0 |
| 200 | **60%** | 45%–74% | `████████████░░░░░░░░` | 10,733 | $0.00131 | 40 | 0 |
| 500 | **60%** | 45%–74% | `████████████░░░░░░░░` | 18,030 | $0.00204 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 16 | 0 | 0 | 1 | 0 | 0 | 0 |
| 50 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| 200 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| 500 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |

## Precision vs reach (cases with a correct existing agent)

| Agents | Only the right agent | Reached the right agent | Mean agents messaged |
|---:|---:|---:|---:|
| 10 | 33% | 71% | 1.75 |
| 50 | 33% | 88% | 1.83 |
| 200 | 33% | 92% | 2.04 |
| 500 | 33% | 96% | 1.92 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 0% | 0% | 0% | 0% |
| explicit followup | 0% | 0% | 0% | 0% |
| implicit followup | 100% | 100% | 100% | 100% |
| new task | 90% | 100% | 100% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (65)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `disambig-alice-wong` | Lunch with Alice Wong | Friday Lunch with Alice, Alice Wong Lunch, Lunch with Alice Wong | wrong agent |
| 10 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 10 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing, Figma Subscription Renewal, Figma Renewal | wrong agent |
| 10 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation | wrong agent |
| 10 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 10 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft, Q4 Planning | wrong agent |
| 10 | `explicit-board-deck` | Board Deck Review with Hannah | Board Deck Review with Hannah, Hannah Board Deck Review, Board Deck Review | wrong agent |
| 10 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Claim Follow-up | wrong agent |
| 10 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 10 | `explicit-gym-fee` | Equinox Membership Cancellation | Equinox Cancellation Follow-up | wrong agent |
| 10 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Landlord Lease Renewal, Lease Renewal Follow-up | wrong agent |
| 10 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Marcus to Grace, Intro Email Marcus and Grace, Marcus Grace Intro | wrong agent |
| 10 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section | wrong agent |
| 10 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Refund Request, Stripe Duplicate Charge | wrong agent |
| 10 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking, Tokyo Trip | wrong agent |
| 10 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Follow-up, Vercel Offer Negotiation, Vercel Job Offer | wrong agent |
| 10 | `new-linear-access` | NEW | (no delegation) | no delegation |
| 50 | `disambig-alice-wong` | Lunch with Alice Wong | Friday Lunch with Alice, Lunch with Alice Wong, Alice Wong Lunch | wrong agent |
| 50 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 50 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing, Figma Subscription Renewal, Figma Renewal | wrong agent |
| 50 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation, Lisbon Hotel Booking | wrong agent |
| 50 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 50 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft | wrong agent |
| 50 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review, Board Deck Review with Hannah, Board Deck Review | wrong agent |
| 50 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Car Insurance Claim, Car Insurance Claim, Geico Claim Follow-up | wrong agent |
| 50 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment | wrong agent |
| 50 | `explicit-gym-fee` | Equinox Membership Cancellation | Equinox Cancellation Follow-up, Equinox Membership Cancellation | wrong agent |
| 50 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Lease Renewal Follow-up, Landlord Lease Renewal | wrong agent |
| 50 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Marcus to Grace, Intro Email Marcus and Grace, Marcus Grace Intro | wrong agent |
| 50 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section, Q3 Report with Priya | wrong agent |
| 50 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge | wrong agent |
| 50 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking | wrong agent |
| 50 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Follow-up, Vercel Offer Negotiation, Vercel Job Offer | wrong agent |
| 200 | `disambig-alice-wong` | Lunch with Alice Wong | Friday Lunch with Alice, Lunch with Alice Wong, Alice Wong Lunch | wrong agent |
| 200 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 200 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing, Figma Renewal, Figma Subscription Renewal | wrong agent |
| 200 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation, Lisbon Hotel Booking, Lisbon Hotel | wrong agent |
| 200 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 200 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft | wrong agent |
| 200 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review, Board Deck Review with Hannah | wrong agent |
| 200 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Car Insurance Claim, Car Insurance Claim, Geico Claim Follow-up | wrong agent |
| 200 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment, Dentist Reminder | wrong agent |
| 200 | `explicit-gym-fee` | Equinox Membership Cancellation | Equinox Cancellation Follow-up, Equinox Membership Cancellation | wrong agent |
| 200 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Landlord Lease Renewal, Lease Renewal Follow-up | wrong agent |
| 200 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Email Marcus and Grace, Intro Marcus to Grace, Marcus Grace Intro | wrong agent |
| 200 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section, Q3 Report with Priya, Q3 Report Follow-up | wrong agent |
| 200 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge, Stripe Refund Request | wrong agent |
| 200 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking, Tokyo Trip Planning, Tokyo Trip | wrong agent |
| 200 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Negotiation, Vercel Offer Follow-up, Vercel Job Offer | wrong agent |
| 500 | `disambig-alice-wong` | Lunch with Alice Wong | Friday Lunch with Alice, Lunch with Alice Wong, Alice Wong Lunch | wrong agent |
| 500 | `disambig-david-kim` | Pricing Update with David Kim | Pricing Update with David Kim, David Kim Pricing Update | wrong agent |
| 500 | `disambig-figma-renewal` | Figma Subscription Renewal | Figma Renewal Pricing, Figma Subscription Renewal, Figma Renewal | wrong agent |
| 500 | `disambig-lisbon-hotel` | Lisbon Hotel Booking | Lisbon Hotel Confirmation, Lisbon Hotel Booking | wrong agent |
| 500 | `disambig-mom-gift` | Mom's Birthday Gift | Mom Birthday Flowers | wrong agent |
| 500 | `disambig-q4-not-q3` | Q4 Planning Doc | Q4 Planning Doc, Q4 Planning Doc Draft, Q4 Planning | wrong agent |
| 500 | `explicit-board-deck` | Board Deck Review with Hannah | Hannah Board Deck Review, Board Deck Review with Hannah | wrong agent |
| 500 | `explicit-car-claim` | Geico Car Insurance Claim | Geico Car Insurance Claim, Car Insurance Claim, Geico Claim Follow-up | wrong agent |
| 500 | `explicit-dentist-when` | Dentist Appointment Reminder | Dentist Appointment, Dentist Reminder, Dentist Appointment Reminder | wrong agent |
| 500 | `explicit-gym-fee` | Equinox Membership Cancellation | Equinox Cancellation Follow-up, Equinox Membership Cancellation | wrong agent |
| 500 | `explicit-landlord-lease` | Lease Renewal with Landlord | Lease Renewal with Landlord, Lease Renewal Follow-up | wrong agent |
| 500 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Email Marcus and Grace, Intro Marcus to Grace, Marcus Grace Intro | wrong agent |
| 500 | `explicit-q3-priya` | Q3 Report with Priya | Priya Q3 Report Section, Q3 Report with Priya | wrong agent |
| 500 | `explicit-stripe-refund` | Stripe Duplicate Charge Refund | Stripe Duplicate Charge Refund, Stripe Duplicate Charge | wrong agent |
| 500 | `explicit-tokyo-hotel` | Tokyo Trip Planning | Tokyo Hotel Booking, Tokyo Trip Planning | wrong agent |
| 500 | `explicit-vercel-offer` | Vercel Job Offer | Vercel Offer Follow-up, Vercel Offer Negotiation, Vercel Job Offer | wrong agent |
