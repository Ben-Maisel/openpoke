# OpenPoke 🌴: agent overload

A fork of [OpenPoke](https://github.com/shlokkhemani/openpoke), a simplified open-source take on [Poke](https://poke.com/). This branch tackles **agent overload**: what breaks as the assistant accumulates agents and history. [problems.md](problems.md) lists every problem found and how each was fixed.

## 1. Where things are

```
server/
├── config.py                      # every tuning knob in one place (env overrides in .env.example)
├── agents/
│   ├── interaction_agent/         # the assistant the user talks to; routes work to agents
│   │   ├── agent.py               # builds its prompt, incl. the working set of relevant agents
│   │   └── tools.py               # send_message_to_agent, create_agent (duplicate check), search_agents
│   └── execution_agent/           # the workers that do the tasks
│       ├── agent.py               # builds a worker's prompt: card + facts + recent runs
│       ├── memory.py              # recent runs within a token budget; tool calls shown as digests
│       ├── card.py                # rewrites an agent's card (summary) after each run
│       ├── facts.py               # records facts (people, threads, drafts, reminders) from tool calls
│       ├── batch_manager.py       # runs workers; one run at a time per agent
│       └── tools/recall.py        # lets a worker search its full history
├── services/
│   ├── execution/
│   │   ├── registry.py            # the agent registry: ID, name, card, facts (SQLite)
│   │   ├── search.py              # picks the working set: recent agents + best matches
│   │   ├── matching.py            # word matching used by search and the duplicate check
│   │   ├── log_store.py           # each agent's full log, one file per ID (a1.log)
│   │   ├── lifecycle.py           # archives agents idle for 30+ days
│   │   └── drafts.py              # drafts are stored, shown and approved by ID before sending
│   ├── notifications.py           # notification lanes: now / digest / silent, quiet hours
│   └── conversation/              # the chat log, and live updates to the browser
└── data/                          # runtime data (git-ignored)
web/                               # Next.js chat UI
tests/                             # pytest suite; the LLM and Gmail are scripted fakes
evals/                             # routing, memory and notification evals
└── results/                       # saved reports for every eval run
```

## 2. Get it running

You need Python 3.10+, Node.js 18+ and an [OpenRouter](https://openrouter.ai/) API key.

```bash
git clone https://github.com/Ben-Maisel/openpoke
cd openpoke
git checkout agent-overload

cp .env.example .env               # then put your OpenRouter key in .env

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r server/requirements-dev.txt
npm install --prefix web
```

Then, in two terminals. Terminal 1 keeps the app's data in a scratch folder, so each try starts empty:

```bash
# terminal 1
export OPENPOKE_DATA_DIR=/tmp/openpoke-demo                 # Windows: $env:OPENPOKE_DATA_DIR = "$env:TEMP\openpoke-demo"
python -m server.server                                     # wait for "Uvicorn running on http://0.0.0.0:8001"

# terminal 2
npm run dev --prefix web
```

Skip the `export` line to keep data in `server/data/` instead.

Open [http://localhost:3000](http://localhost:3000).

Gmail is optional: add your [Composio](https://composio.dev/) keys to `.env` and connect it under *Settings → Gmail*. Without it, chat and reminders work, and the email tools report that Gmail isn't connected.

## 3. Try it

Send these in the chat and watch the server terminal:

1. *"Remind me to call Sally tomorrow at 9am."* The server logs `Created agent a1`.
2. *"Actually make it 10am."* The same agent `a1` is reused, not a new one.
3. Open a second tab: messages appear in both instantly.

Then look in the data folder's `execution_agents/` (`/tmp/openpoke-demo/execution_agents/` with the scratch folder above): `registry.db` holds each agent's ID, name, card and facts, and `a1.log` holds its full tool-call records.

## 4. Run the tests

```bash
python -m pytest
```

About 1,100 tests, no API keys needed: they cover each part on its own and full conversations end to end, with the LLM and Gmail replaced by scripted fakes. CI runs them on Linux and Windows (Python 3.10 and 3.12), and type-checks and builds the web app.

## 5. Evals

These call the real model (Claude Haiku 5.5 by default), so they need the OpenRouter key. Each writes a `report.md` to `evals/results/<label>/`. Add `--report-only` to rebuild a report from its saved log with no API calls.

| Command | What it measures | Before → after | Results |
|---|---|---|---|
| `python -m evals.routing --variant messy` | Is each request sent to the right agent, with look-alike agent names and up to 500 agents? | 68% → **92%** at 500 agents; prompt 14.4k → **6.9k** tokens | `baseline-messy`, `p2-messy` |
| `python -m evals.memory` | Can a long-lived agent answer questions about its own past work after 200 runs? | 60% → **100%**; prompt 71.7k → **12.3k** tokens | `memory`, `memory-v2` |
| `python -m evals.notifications.day` | How many messages does a scripted busy day send the user? (no LLM) | 28 → **11** messages | `notifications-day` |
| `python -m evals.notifications.urgency` | Do only urgent emails interrupt the user? | 0 missed urgent emails, 0 false interrupts | `notifications-urgency` |

Without `--variant messy`, the routing eval uses distinct agent names (`baseline`, `p2`). The routing labels are the stages of the work: `baseline` is the original app, `p4` adds the duplicate check, `p2` adds the working set and cards.

## License
MIT — see [LICENSE](LICENSE).
