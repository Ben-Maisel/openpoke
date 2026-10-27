"""Build a readable report from a logged memory-eval run (no API calls)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .run import read_answers
from .scoring import CORRECT, ERROR, wilson_interval

_EXCERPT_CHARS = 160


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _accuracy(records: Sequence[Dict[str, Any]]) -> str:
    scored = [r for r in records if r["outcome"] != ERROR]
    if not scored:
        return "–"
    correct = sum(r["outcome"] == CORRECT for r in scored)
    low, high = wilson_interval(correct, len(scored))
    return f"**{_pct(correct / len(scored))}** ({_pct(low)}–{_pct(high)})"


def _excerpt(text: str) -> str:
    flat = " ".join(text.split()).replace("|", "/")
    return flat if len(flat) <= _EXCERPT_CHARS else f"{flat[:_EXCERPT_CHARS]}…"


def write_report(out_dir: Path) -> Path:
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    records = list(read_answers(out_dir / "answers.jsonl").values())
    cards_path = out_dir / "cards.json"
    cards: Dict[str, List[str]] = json.loads(cards_path.read_text(encoding="utf-8")) if cards_path.exists() else {}
    identity = meta["identity"]
    lengths = sorted({r["length"] for r in records})
    conditions = [c for c in ("before", "after") if any(r["condition"] == c for r in records)]

    def pick(length: int, condition: str, category: str = "") -> List[Dict[str, Any]]:
        return [r for r in records if r["length"] == length and r["condition"] == condition
                and (not category or r["category"] == category)]

    lines: List[str] = [
        f"# Memory eval: `{meta['label']}`",
        "",
        "One long-lived worker answers questions about its own past runs. **before** is the original app "
        "(whole journal in the prompt, tool data cut to 200/500 characters, no recall); **after** is the "
        "current app (card, facts, recent runs within the history budget, and the recall tool). Live tools "
        "are unavailable in both, so answers come from memory.",
        "",
        f"- **Model:** `{identity['model']}` · **card model:** `{identity['card_model']}` · **seed** {identity['seed']} "
        f"· **history budget:** {identity['history_token_budget']} tokens",
        f"- **Fingerprints:** dataset `{identity['dataset_sha']}` · app `{identity['app_sha']}` · legacy `{identity['legacy_sha']}`",
        f"- **Code:** `{meta.get('git_commit', 'unknown')[:10]}`{' (uncommitted changes)' if meta.get('git_dirty') else ''}",
        f"- **Answers:** {len(records)} · **API errors:** {sum(r['outcome'] == ERROR for r in records)} · "
        f"**total cost:** ${sum(r['cost'] for r in records):.4f}",
        "",
        "## Accuracy and prompt size by journal length",
        "",
        "Prompt tokens are for the worker's first call, i.e. the memory it starts from (95% CI in brackets).",
        "",
        "| Runs in journal | " + " | ".join(f"{c} accuracy" for c in conditions) + " | "
        + " | ".join(f"{c} prompt tokens" for c in conditions) + " | after: answers using recall |",
        "|---:|" + "---:|" * (2 * len(conditions) + 1),
    ]
    for length in lengths:
        accuracy = [_accuracy(pick(length, c)) for c in conditions]
        tokens = [f"{_mean([r['first_prompt_tokens'] for r in pick(length, c)]):,.0f}" for c in conditions]
        after = pick(length, "after")
        recall_use = _pct(_mean([r["recalls"] > 0 for r in after])) if after else "–"
        lines.append(f"| {length} | " + " | ".join(accuracy) + " | " + " | ".join(tokens) + f" | {recall_use} |")

    categories = sorted({r["category"] for r in records})
    lines += ["", "## Accuracy by question category", "",
              "| Category | " + " | ".join(f"{c} @ {n}" for n in lengths for c in conditions) + " |",
              "|---|" + "---:|" * (len(lengths) * len(conditions))]
    for category in categories:
        cells = []
        for length in lengths:
            for condition in conditions:
                scored = [r for r in pick(length, condition, category) if r["outcome"] != ERROR]
                cells.append(_pct(_mean([r["outcome"] == CORRECT for r in scored])) if scored else "–")
        lines.append(f"| {category.replace('_', ' ')} | " + " | ".join(cells) + " |")

    if cards:
        lines += ["", "## The card the real refresh produced (after the last run)", ""]
        for length in sorted(cards, key=int):
            lines.append(f"- **{length} runs:** {cards[length][-1]}")

    misses = sorted((r for r in records if r["outcome"] != CORRECT),
                    key=lambda r: (r["length"], r["condition"], r["question_id"]))
    lines += ["", f"## Every miss ({len(misses)})", ""]
    if misses:
        lines += ["| Runs | Condition | Question | Expected | Answer | Recalls |", "|---:|---|---|---|---|---:|"]
        for r in misses:
            expected = " + ".join(" / ".join(group) for group in r["expect"])
            got = _excerpt(r["answer"]) if r["answer"] else f"(error: {_excerpt(r['error'] or '')})"
            lines.append(f"| {r['length']} | {r['condition']} | `{r['question_id']}` | {expected} | {got} | {r['recalls']} |")
    else:
        lines.append("None.")

    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
