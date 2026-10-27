"""Build a readable report from a logged routing-eval run (no API calls)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .run import read_decisions, to_results
from .scoring import Outcome, summarize

_OUTCOME_ORDER = [o for o in Outcome if o is not Outcome.CORRECT]
_BAR_WIDTH = 20


def _bar(fraction: float) -> str:
    filled = round(fraction * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def write_report(out_dir: Path) -> Path:
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    records = list(read_decisions(out_dir / "decisions.jsonl").values())
    results = to_results(records)
    by_size = summarize(results, key=lambda r: r.roster_size)
    by_category_size = summarize(results, key=lambda r: (r.category, r.roster_size))
    identity = meta["identity"]

    lines: List[str] = [
        f"# Routing eval: `{meta['label']}`",
        "",
        f"- **Model:** `{identity['model']}` · **seed** {identity['seed']} · "
        f"**dataset** `{identity['dataset_sha']}` · **app prompt** `{identity['app_prompt_sha']}`",
        f"- **Roster variant:** {identity.get('variant', 'clean')}"
        + (" (two duplicate decoy agents per case; see `evals/routing/data/decoys.json`)" if identity.get("variant") == "messy" else "")
        + (" · **agent cards:** yes (`evals/routing/data/cards.json`)" if identity.get("cards_sha") else " · **agent cards:** none"),
        f"- **Code:** `{meta.get('git_commit', 'unknown')[:10]}`{' (uncommitted changes)' if meta.get('git_dirty') else ''}",
        f"- **Decisions:** {len(results)} · **total cost:** ${sum(r.cost for r in results):.4f} · "
        f"**duplicate warnings shown:** {sum(r.get('duplicate_warnings', 0) for r in records)} · "
        f"**agent searches:** {sum(r.get('searches', 0) for r in records)}",
        "",
        "## Accuracy by roster size",
        "",
        "| Agents | Accuracy | 95% CI | | Mean prompt tokens | Cost / decision | n | API errors |",
        "|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for size, s in by_size.items():
        per = (sum(r.cost for r in results if r.roster_size == size) / (s.n + s.errors)) if s.n + s.errors else 0
        lines.append(
            f"| {size} | **{_pct(s.accuracy)}** | {_pct(s.ci_low)}–{_pct(s.ci_high)} | `{_bar(s.accuracy)}` "
            f"| {s.mean_prompt_tokens:,.0f} | ${per:.5f} | {s.n} | {s.errors} |"
        )

    lines += ["", "## What went wrong (count of decisions)", "",
              "| Agents | " + " | ".join(o.value.replace("_", " ") for o in _OUTCOME_ORDER) + " | near misses |",
              "|---:|" + "---:|" * (len(_OUTCOME_ORDER) + 1)]
    for size, s in by_size.items():
        lines.append(f"| {size} | " + " | ".join(str(s.outcomes[o.value]) for o in _OUTCOME_ORDER) + f" | {s.near_misses} |")

    # Exact accuracy hides a trade-off when the model hedges by messaging several look-alike agents:
    # it reaches the right one more often, at the cost of extra runs (and risk, for actions).
    lines += ["", "## Precision vs reach (cases with a correct existing agent)", "",
              "| Agents | Only the right agent | Reached the right agent | Mean agents messaged |",
              "|---:|---:|---:|---:|"]
    for size in by_size:
        agent_cases = [r for r in records if r["roster_size"] == size and r["expect"]["kind"] == "agent"
                       and r["outcome"] != Outcome.ERROR.value]
        if not agent_cases:
            continue
        exact = sum(r["outcome"] == Outcome.CORRECT.value for r in agent_cases) / len(agent_cases)
        reached = sum(bool(set(r["expect"]["agents"]) & set(r["routes"])) for r in agent_cases) / len(agent_cases)
        delegated = [len(r["routes"]) for r in agent_cases if r["routes"]]
        fan = sum(delegated) / len(delegated) if delegated else 0.0
        lines.append(f"| {size} | {_pct(exact)} | {_pct(reached)} | {fan:.2f} |")

    sizes = list(by_size)
    categories = sorted({c for c, _ in by_category_size})
    lines += ["", "## Accuracy by case category", "",
              "| Category | " + " | ".join(f"{size} agents" for size in sizes) + " |",
              "|---|" + "---:|" * len(sizes)]
    for category in categories:
        cells = [_pct(by_category_size[(category, size)].accuracy) if (category, size) in by_category_size else "–"
                 for size in sizes]
        lines.append(f"| {category.replace('_', ' ')} | " + " | ".join(cells) + " |")

    failures = sorted((r for r in records if r["outcome"] != Outcome.CORRECT.value),
                      key=lambda r: (r["roster_size"], r["category"], r["case_id"]))
    lines += ["", f"## Every miss ({len(failures)})", ""]
    if failures:
        lines += ["| Agents | Case | Expected | Got | Outcome |", "|---:|---|---|---|---|"]
        for r in failures:
            expected = ", ".join(r["expect"]["agents"]) or r["expect"]["kind"].upper()
            got = ", ".join(r["routes"]) or (r["error"] or "(no delegation)")
            lines.append(f"| {r['roster_size']} | `{r['case_id']}` | {expected} | {got} | {r['outcome'].replace('_', ' ')} |")
    else:
        lines.append("None.")

    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
