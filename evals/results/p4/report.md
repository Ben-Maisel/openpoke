# Routing eval: `p4`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `68e83b09628f4de3`
- **Roster variant:** clean
- **Code:** `09cb2b743d`
- **Decisions:** 160 · **total cost:** $0.1900 · **duplicate warnings shown:** 0

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **98%** | 87%–100% | `████████████████████` | 6,112 | $0.00076 | 40 | 0 |
| 50 | **100%** | 91%–100% | `████████████████████` | 6,580 | $0.00081 | 40 | 0 |
| 200 | **98%** | 87%–100% | `████████████████████` | 10,967 | $0.00126 | 40 | 0 |
| 500 | **100%** | 91%–100% | `████████████████████` | 17,627 | $0.00192 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 200 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Precision vs reach (cases with a correct existing agent)

| Agents | Only the right agent | Reached the right agent | Mean agents messaged |
|---:|---:|---:|---:|
| 10 | 100% | 100% | 1.00 |
| 50 | 100% | 100% | 1.00 |
| 200 | 100% | 100% | 1.00 |
| 500 | 100% | 100% | 1.00 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 100% | 100% | 100% | 100% |
| explicit followup | 100% | 100% | 100% | 100% |
| implicit followup | 100% | 100% | 100% | 100% |
| new task | 90% | 100% | 90% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (2)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `new-leaking-sink` | NEW | Rent Payment Reminder | wrongly reused |
| 200 | `new-linear-access` | NEW | (no delegation) | no delegation |
