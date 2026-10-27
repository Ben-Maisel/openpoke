# A day in the life: interruptions before and after notification lanes

One scripted day, run minute by minute through the real notification center (digest every 60 minutes, quiet hours 22:00-07:00). Deterministic: no LLM.

- **Events:** 33: 15 emails (3 urgent, 7 important, 5 unimportant), 2 reminders, and an hourly background check from 07:00 to 22:00 that finds news once.

| | Messages to the user | Of which interrupt at once | Digests |
|---|---:|---:|---:|
| Before (original app) | 28 | 28 | 0 |
| After (lanes) | **11** | 5 | 6 |

- Longest wait for a digest item during the day: 1:00:00. The 22:30 email waited 8:30:00 because of quiet hours, and was delivered at 07:00.
- Background checks with nothing new (15 of 16) never reached the user; before, each one was a message.

## Every message the user received (after)

| Time | Message |
|---|---|
| Tue 09:00 | Reminder: call the dentist |
| Tue 09:10 | Interview moved to 3pm today |
| Tue 09:20 | Digest of background updates (1, held so the user isn't interrupted for each): / - Priya: slides due Friday |
| Tue 11:05 | Digest of background updates (2, held so the user isn't interrupted for each): / - Checkup scheduled Nov 18 / - Final round next week |
| Tue 13:30 | Digest of background updates (1, held so the user isn't interrupted for each): / - Lease renewal offer |
| Tue 13:40 | Chase verification code |
| Tue 15:15 | Digest of background updates (2, held so the user isn't interrupted for each): / - Wedding RSVP by Dec 1 / - Alice replied: Friday works |
| Tue 17:05 | Wire approval needed before 5pm ET |
| Tue 17:45 | Digest of background updates (1, held so the user isn't interrupted for each): / - Kitchen renovation quote |
| Tue 18:00 | Reminder: take medication |
| Wed 07:00 | Digest of background updates (1, held so the user isn't interrupted for each): / - Benefits enrollment closes Nov 15 |
