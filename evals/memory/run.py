"""Run the long-lived worker memory eval, logging every answer so each one only ever runs once.

Results live in evals/results/<label>/:
  meta.json        what was run: models, seed, dataset / legacy / app fingerprints, git commit
  cards.json       the card the real refresh produced after every run of each journal (built once)
  answers.jsonl    one line per answer, appended as it completes (raw model output included)
  report.md        regenerated from answers.jsonl; never needs the API

Re-running the same label resumes: cards and answers already logged are reused; only API errors are retried.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from server.agents.execution_agent import card, memory
from server.agents.execution_agent.agent import SYSTEM_PROMPT_TEMPLATE
from server.agents.execution_agent.tools import get_tool_schemas
from server.agents.execution_agent.tools import recall
from server.config import get_settings

from .dataset import DATA_DIR, DEFAULT_SEED, JOURNAL_LENGTHS, Question, agent_name, build_journal, load_questions
from .harness import CONDITIONS, ask, build_card, eval_patches
from .scoring import ERROR, classify

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
AnswerKey = Tuple[str, int, str, int]  # (question_id, journal_length, condition, repeat)


class RunMismatchError(RuntimeError):
    """The results folder belongs to a run with a different model, seed, dataset, or app code."""


@dataclass(frozen=True)
class RunConfig:
    label: str
    model: str
    card_model: str
    lengths: Tuple[int, ...] = JOURNAL_LENGTHS
    conditions: Tuple[str, ...] = CONDITIONS
    repeats: int = 1
    seed: int = DEFAULT_SEED


# --- Fingerprints: what makes two runs comparable -------------------------------

def _sha(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def dataset_fingerprint() -> str:
    return _sha(*((DATA_DIR / name).read_text(encoding="utf-8") for name in ("story.json", "filler.json", "questions.json")))


def app_fingerprint() -> str:
    """The current app's memory: worker prompt, tool schemas, rendering, recall, and the card prompt."""
    return _sha(SYSTEM_PROMPT_TEMPLATE, json.dumps(get_tool_schemas(), sort_keys=True),
                inspect.getsource(memory), inspect.getsource(recall), card.CARD_SYSTEM_PROMPT, _memory_settings())


def _memory_settings() -> str:
    """The settings that shape worker memory (sizes of cards, digests and recall)."""
    settings = get_settings()
    keys = sorted(k for k in type(settings).model_fields
                  if k.startswith(("card_", "digest_", "recall_", "chars_per_token", "facts_per_call")))
    return json.dumps({k: getattr(settings, k) for k in keys})


def run_identity(config: RunConfig) -> Dict[str, Any]:
    return {
        "model": config.model, "card_model": config.card_model, "seed": config.seed,
        "dataset_sha": dataset_fingerprint(), "app_sha": app_fingerprint(),
        "legacy_sha": _sha((DATA_DIR / "legacy_system_prompt.md").read_text(encoding="utf-8")),
        "history_token_budget": get_settings().execution_history_token_budget,
    }


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Reading the log --------------------------------------------------------------

def read_answers(path: Path) -> Dict[AnswerKey, Dict[str, Any]]:
    """Latest record per answer, preferring a real answer over an API error."""
    latest: Dict[AnswerKey, Dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        key = (record["question_id"], record["length"], record["condition"], record["repeat"])
        previous = latest.get(key)
        if previous is None or previous["outcome"] == ERROR or record["outcome"] != ERROR:
            latest[key] = record
    return latest


# --- Cards: the real refresh, run once over each journal ----------------------------

async def ensure_cards(config: RunConfig, out_dir: Path, *, progress: bool) -> Dict[str, List[str]]:
    path = out_dir / "cards.json"
    cards: Dict[str, List[str]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    missing = [n for n in config.lengths if str(n) not in cards]
    if missing and "after" in config.conditions:
        if progress:
            print(f"[{config.label}] building cards for journals of {missing} runs (one refresh per run)")
        trails = await asyncio.gather(*(build_card(agent_name(), build_journal(n, config.seed)) for n in missing))
        cards.update({str(n): trail for n, trail in zip(missing, trails)})
        path.write_text(json.dumps(cards, indent=1, ensure_ascii=False), encoding="utf-8")
    return cards


# --- Running -------------------------------------------------------------------------

async def run_eval(
    config: RunConfig,
    questions: Sequence[Question],
    out_dir: Path,
    *,
    api_key: Optional[str],
    concurrency: int = 8,
    progress: bool = True,
) -> Dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path, answers_path = out_dir / "meta.json", out_dir / "answers.jsonl"

    identity = run_identity(config)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if meta and meta["identity"] != identity:
        raise RunMismatchError(
            f"{out_dir} holds a run with {meta['identity']}, not {identity}. Use a new --label to start a fresh run."
        )
    meta = meta or {"identity": identity, "label": config.label, "created_at": _now()}
    meta.update({
        "lengths": list(config.lengths), "conditions": list(config.conditions), "repeats": config.repeats,
        "question_ids": [q.id for q in questions],
        "git_commit": _git("rev-parse", "HEAD"), "git_dirty": bool(_git("status", "--porcelain")),
        "last_started_at": _now(),
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    with eval_patches():
        cards = await ensure_cards(config, out_dir, progress=progress)
        journals = {n: build_journal(n, config.seed) for n in config.lengths}

        done = {k for k, r in read_answers(answers_path).items() if r["outcome"] != ERROR}
        jobs = [(q, n, c, rep) for q in questions for n in config.lengths for c in config.conditions
                for rep in range(config.repeats) if (q.id, n, c, rep) not in done]
        total = len(questions) * len(config.lengths) * len(config.conditions) * config.repeats
        stats = {"skipped": total - len(jobs), "ran": 0, "errors": 0}
        if progress:
            print(f"[{config.label}] {len(jobs)} answers to run, {stats['skipped']} already logged")

        semaphore, write_lock = asyncio.Semaphore(concurrency), asyncio.Lock()

        async def one(question: Question, length: int, condition: str, repeat: int) -> None:
            card_text = cards[str(length)][-1] if condition == "after" else ""
            async with semaphore:
                started = time.monotonic()
                answer = await ask(question, journals[length], condition, name=agent_name(), card=card_text,
                                   model=config.model, api_key=api_key)
                duration = time.monotonic() - started
            outcome = classify(answer.text, answer.error, question.expect)
            record = {
                "question_id": question.id, "category": question.category, "source": question.source,
                "length": length, "condition": condition, "repeat": repeat,
                "expect": [list(group) for group in question.expect], "outcome": outcome,
                "answer": answer.text, "error": answer.error, "steps": answer.steps,
                "tools_called": answer.tools_called, "recalls": answer.recalls,
                "first_prompt_tokens": answer.first_prompt_tokens, "prompt_tokens": answer.prompt_tokens,
                "completion_tokens": answer.completion_tokens, "cost": answer.cost,
                "system_prompt_chars": answer.system_prompt_chars, "duration_s": round(duration, 2),
                "logged_at": _now(), "raw_responses": answer.raw_responses,
            }
            async with write_lock:
                with answers_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                stats["ran"] += 1
                stats["errors"] += outcome == ERROR
                if progress:
                    print(f"  {stats['ran']:>3}/{len(jobs)}  {length:>3} runs  {condition:<6} {question.id:<20} {outcome}")

        await asyncio.gather(*(one(*job) for job in jobs))

    meta["last_finished_at"] = _now()
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return stats


# --- CLI --------------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> None:
    from .report import write_report

    parser = argparse.ArgumentParser(prog="python -m evals.memory", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="memory", help="results folder name (default: 'memory')")
    parser.add_argument("--model", help="OpenRouter model id (default: the app's execution agent model)")
    parser.add_argument("--lengths", default=",".join(map(str, JOURNAL_LENGTHS)), help="journal lengths, comma-separated")
    parser.add_argument("--conditions", default=",".join(CONDITIONS), help="before, after, or both")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--smoke", action="store_true", help="one question per category on the shortest journal")
    parser.add_argument("--report-only", action="store_true", help="rebuild report.md from the log; no API calls")
    args = parser.parse_args(argv)

    settings = get_settings()
    label = "smoke-memory" if args.smoke and args.label == "memory" else args.label
    out_dir = RESULTS_DIR / label
    if not args.report_only:
        lengths = tuple(int(n) for n in args.lengths.split(","))
        questions = load_questions()
        if args.smoke:
            seen: set = set()
            questions = [q for q in questions if not (q.category in seen or seen.add(q.category))]
            lengths = lengths[:1]
        config = RunConfig(label=label, model=args.model or settings.execution_agent_model,
                           card_model=settings.summarizer_model, lengths=lengths,
                           conditions=tuple(args.conditions.split(",")), repeats=args.repeats, seed=args.seed)
        stats = asyncio.run(run_eval(config, questions, out_dir, api_key=settings.openrouter_api_key,
                                     concurrency=args.concurrency))
        print(f"[{label}] ran {stats['ran']} ({stats['errors']} API errors), skipped {stats['skipped']} already logged")
    print(f"[{label}] report: {write_report(out_dir)}")


if __name__ == "__main__":  # pragma: no cover
    main()
