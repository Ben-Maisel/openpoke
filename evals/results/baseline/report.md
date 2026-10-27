# Routing eval: `baseline`

- **Model:** `anthropic/claude-haiku-5.5` · **seed** 20261026 · **dataset** `9b67db241db260ce` · **app prompt** `44a60bb84fac425a`
- **Code:** `486c12033a`
- **Decisions:** 160 · **total cost:** $0.1654

## Accuracy by roster size

| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |
|---:|---:|---:|---|---:|---:|---:|---:|
| 10 | **98%** | 87%–100% | `████████████████████` | 5,059 | $0.00066 | 40 | 0 |
| 50 | **100%** | 91%–100% | `████████████████████` | 5,956 | $0.00076 | 40 | 0 |
| 200 | **100%** | 91%–100% | `████████████████████` | 9,283 | $0.00109 | 40 | 0 |
| 500 | **98%** | 87%–100% | `████████████████████` | 14,416 | $0.00163 | 40 | 0 |

## What went wrong (count of decisions)

| Agents | wrong agent | duplicate | wrongly reused | no delegation | unneeded delegation | error | near misses |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |

## Accuracy by case category

| Category | 10 agents | 50 agents | 200 agents | 500 agents |
|---|---:|---:|---:|---:|
| disambiguation | 100% | 100% | 100% | 100% |
| explicit followup | 100% | 100% | 100% | 100% |
| implicit followup | 100% | 100% | 100% | 88% |
| new task | 90% | 100% | 100% | 100% |
| no delegation | 100% | 100% | 100% | 100% |

## Every miss (2)

| Agents | Case | Expected | Got | Outcome |
|---:|---|---|---|---|
| 10 | `new-leaking-sink` | NEW | Rent Payment Reminder | wrongly reused |
| 500 | `implicit-friendlier` | Overdue Invoice Email to Nina | (no delegation) | no delegation |
