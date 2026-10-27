# Urgency eval: `notifications-urgency`

The app's own email classifier judges 24 labelled emails (8 urgent, 8 important, 8 not important). Its verdict decides the lane: urgent → now (interrupts), important → digest, otherwise nothing.

- **Model:** `anthropic/claude-haiku-5.5` · **dataset** `dd5429a6586a7bde` · **classifier prompt** `fd4e60debef456dd` · **code** `306e8515c7`
- **Judgements:** 72 · **cost:** $0.0146

| Measure | Result |
|---|---|
| Exact lane | **96%** (69/72, CI 88%–99%) |
| Missed urgent (urgent email didn't interrupt) | **0%** (0/24, CI 0%–14%) |
| False interrupts (non-urgent email interrupted) | **0%** (0/48, CI 0%–7%) |
| Dropped (important email not surfaced at all) | **12%** (3/24, CI 4%–31%) |

## Where each label went

| Label | now | digest | none |
|---|---:|---:|---:|
| urgent | 24 | 0 | 0 |
| important | 0 | 21 | 3 |
| not important | 0 | 0 | 24 |

## Every mistake (3)

| Email | Label | Expected | Got | Subject | Classifier's summary |
|---|---|---|---|---|---|
| i3 | important | digest | none | Your annual checkup is scheduled |  |
| i3 | important | digest | none | Your annual checkup is scheduled |  |
| i3 | important | digest | none | Your annual checkup is scheduled |  |
