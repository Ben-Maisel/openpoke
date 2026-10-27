# Routing eval: `p2-messy`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `0ad7ea6a2449654e`
- **Roster variant:** messy (two duplicate decoy agents per case; see `evals/routing/data/decoys.json`) · **agent cards:** yes (`evals/routing/data/cards.json`)
- **Code:** `4052f0d556` (uncommitted changes)
- **Decisions:** 160 · **total cost:** $0.1426 · **duplicate warnings shown:** 0 · **agent searches:** 18

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **88%** | 74%–95% | `██████████████████░░` | 7,525 | $0.00093 | 40 | 0 |
| 50 | **90%** | 77%–96% | `██████████████████░░` | 6,893 | $0.00086 | 40 | 0 |
| 200 | **90%** | 77%–96% | `██████████████████░░` | 6,915 | $0.00087 | 40 | 0 |
| 500 | **92%** | 80%–97% | `██████████████████░░` | 7,391 | $0.00092 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 1 | 0 | 1 | 3 | 0 | 0 | 0 |
| 50 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| 200 | 0 | 0 | 1 | 3 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |

## Precision vs reach (cases with a correct existing agent)

| Agents | Only the right agent | Reached the right agent | Mean agents messaged |
|---:|---:|---:|---:|
| 10 | 83% | 88% | 1.05 |
| 50 | 83% | 83% | 1.00 |
| 200 | 88% | 88% | 1.00 |
| 500 | 88% | 88% | 1.00 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 100% | 83% | 100% | 100% |
| explicit followup | 60% | 70% | 70% | 70% |
| implicit followup | 100% | 100% | 100% | 100% |
| new task | 90% | 100% | 90% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (16)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 10 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 10 | `explicit-marcus-intro` | Intro Marcus to Grace | Intro Marcus to Grace, Marcus Grace Intro | wrong agent |
| 10 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 10 | `new-linear-access` | NEW | Email to Marcus Johnson | wrongly reused |
| 50 | `disambig-mom-gift` | Mom's Birthday Gift | (no delegation) | no delegation |
| 50 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 50 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 50 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 200 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 200 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 200 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 200 | `new-linear-access` | NEW | API Migration with Jake | wrongly reused |
| 500 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 500 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 500 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
