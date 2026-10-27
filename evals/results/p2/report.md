# Routing eval: `p2`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `0ad7ea6a2449654e`
- **Roster variant:** clean · **agent cards:** yes (`evals/routing/data/cards.json`)
- **Code:** `4052f0d556`
- **Decisions:** 160 · **total cost:** $0.1382 · **duplicate warnings shown:** 0 · **agent searches:** 15

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **90%** | 77%–96% | `██████████████████░░` | 6,622 | $0.00082 | 40 | 0 |
| 50 | **85%** | 71%–93% | `█████████████████░░░` | 7,123 | $0.00087 | 40 | 0 |
| 200 | **90%** | 77%–96% | `██████████████████░░` | 7,472 | $0.00091 | 40 | 0 |
| 500 | **90%** | 77%–96% | `██████████████████░░` | 6,913 | $0.00085 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| 50 | 0 | 0 | 0 | 6 | 0 | 0 | 0 |
| 200 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 4 | 0 | 0 | 0 |

## Precision vs reach (cases with a correct existing agent)

| Agents | Only the right agent | Reached the right agent | Mean agents messaged |
|---:|---:|---:|---:|
| 10 | 83% | 83% | 1.00 |
| 50 | 75% | 75% | 1.00 |
| 200 | 83% | 83% | 1.00 |
| 500 | 83% | 83% | 1.00 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 83% | 50% | 83% | 83% |
| explicit followup | 70% | 70% | 70% | 70% |
| implicit followup | 100% | 100% | 100% | 100% |
| new task | 100% | 100% | 100% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (18)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `disambig-mom-gift` | Mom's Birthday Gift | (no delegation) | no delegation |
| 10 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 10 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 10 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 50 | `disambig-david-kim` | Pricing Update with David Kim | (no delegation) | no delegation |
| 50 | `disambig-mom-gift` | Mom's Birthday Gift | (no delegation) | no delegation |
| 50 | `disambig-q4-not-q3` | Q4 Planning Doc | (no delegation) | no delegation |
| 50 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 50 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 50 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 200 | `disambig-mom-gift` | Mom's Birthday Gift | (no delegation) | no delegation |
| 200 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 200 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 200 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
| 500 | `disambig-mom-gift` | Mom's Birthday Gift | (no delegation) | no delegation |
| 500 | `explicit-dentist-when` | Dentist Appointment Reminder | (no delegation) | no delegation |
| 500 | `explicit-gym-fee` | Equinox Membership Cancellation | (no delegation) | no delegation |
| 500 | `explicit-tokyo-hotel` | Tokyo Trip Planning | (no delegation) | no delegation |
