"""Run the routing eval, logging every decision so each one only ever runs once.

Results live in evals/results/<label>/:
  meta.json        what was run: model, seed, dataset and app-prompt fingerprints, git commit
  rosters.json     the exact roster each case saw at each size
  decisions.jsonl  one line per decision, appended as it completes (raw model output included)
  report.md        regenerated from decisions.jsonl; never needs the API

Re-running the same label resumes: logged decisions are skipped and only API errors are retried.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from server.agents.interaction_agent.agent import build_system_prompt
from server.agents.interaction_agent.tools import get_tool_schemas
from server.config import get_settings

from .cases import RoutingCase
from .dataset import DATA_DIR, DEFAULT_SEED, ROSTER_SIZES, build_roster, load_cases, load_decoys
from .harness import run_decision
from .scoring import CaseResult, Outcome, classify, is_near_miss

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
DecisionKey = Tuple[str, int, int]  # (case_id, roster_size, repeat)


class RunMismatchError(RuntimeError):
    """The results folder belongs to a run with a different model, seed, dataset, or app prompt."""


@dataclass(frozen=True)
class RunConfig:
    label: str
    model: str
    sizes: Tuple[int, ...] = ROSTER_SIZES
    repeats: int = 1
    seed: int = DEFAULT_SEED
    variant: str = "clean"  # "messy" adds duplicate decoy agents from data/decoys.json


# --- Fingerprints: what makes two runs comparable -------------------------------

def _sha(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def dataset_fingerprint() -> str:
    return _sha(*(p.read_text(encoding="utf-8") for p in (DATA_DIR / "cases.json", DATA_DIR / "world.json")))


def app_prompt_fingerprint() -> str:
    return _sha(build_system_prompt(), json.dumps(get_tool_schemas(), sort_keys=True))


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def run_identity(config: RunConfig) -> Dict[str, Any]:
    identity = {
        "model": config.model,
        "seed": config.seed,
        "dataset_sha": dataset_fingerprint(),
        "app_prompt_sha": app_prompt_fingerprint(),
    }
    if config.variant != "clean":  # clean runs keep the original identity shape
        identity["variant"] = config.variant
        identity["decoys_sha"] = _sha((DATA_DIR / "decoys.json").read_text(encoding="utf-8"))
    return identity


# --- Reading the log --------------------------------------------------------------

def read_decisions(path: Path) -> Dict[DecisionKey, Dict[str, Any]]:
    """Latest record per decision, preferring a successful record over an API error."""
    latest: Dict[DecisionKey, Dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        key = (record["case_id"], record["roster_size"], record["repeat"])
        previous = latest.get(key)
        if previous is None or previous["outcome"] == Outcome.ERROR.value or record["outcome"] != Outcome.ERROR.value:
            latest[key] = record
    return latest


def to_results(records: Iterable[Dict[str, Any]]) -> List[CaseResult]:
    return [
        CaseResult(
            case_id=r["case_id"], category=r["category"], roster_size=r["roster_size"],
            outcome=Outcome(r["outcome"]), routes=r["routes"], near_miss=r["near_miss"],
            prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
            cost=r["cost"], repeat=r["repeat"],
        )
        for r in records
    ]


# --- Running -------------------------------------------------------------------------

def select_smoke_cases(cases: Sequence[RoutingCase], per_category: int = 2) -> List[RoutingCase]:
    seen: Counter = Counter()
    chosen = []
    for case in cases:
        if seen[case.category] < per_category:
            chosen.append(case)
            seen[case.category] += 1
    return chosen


async def run_eval(
    config: RunConfig,
    cases: Sequence[RoutingCase],
    out_dir: Path,
    *,
    api_key: Optional[str],
    concurrency: int = 8,
    progress: bool = True,
) -> Dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path, decisions_path = out_dir / "meta.json", out_dir / "decisions.jsonl"

    identity = run_identity(config)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if meta and meta["identity"] != identity:
        raise RunMismatchError(
            f"{out_dir} holds a run with {meta['identity']}, not {identity}. Use a new --label to start a fresh run."
        )
    meta = meta or {"identity": identity, "label": config.label, "created_at": _now()}
    meta.update({
        "sizes": list(config.sizes), "repeats": config.repeats, "case_ids": [c.id for c in cases],
        "git_commit": _git("rev-parse", "HEAD"), "git_dirty": bool(_git("status", "--porcelain")),
        "last_started_at": _now(),
    })
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    decoys = load_decoys() if config.variant == "messy" else {}
    rosters = {
        c.id: {str(size): build_roster(c, size, config.seed, decoys.get(c.id, ())) for size in config.sizes}
        for c in cases
    }
    (out_dir / "rosters.json").write_text(json.dumps(rosters, indent=1), encoding="utf-8")

    done = {k for k, r in read_decisions(decisions_path).items() if r["outcome"] != Outcome.ERROR.value}
    jobs = [(c, size, rep) for c in cases for size in config.sizes for rep in range(config.repeats)
            if (c.id, size, rep) not in done]
    stats = {"skipped": len(cases) * len(config.sizes) * config.repeats - len(jobs), "ran": 0, "errors": 0}
    if progress:
        print(f"[{config.label}] {len(jobs)} decisions to run, {stats['skipped']} already logged")

    semaphore, write_lock = asyncio.Semaphore(concurrency), asyncio.Lock()

    async def one(case: RoutingCase, size: int, repeat: int) -> None:
        roster = rosters[case.id][str(size)]
        async with semaphore:
            started = time.monotonic()
            decision = await run_decision(case, roster, model=config.model, api_key=api_key)
            duration = time.monotonic() - started
        outcome = classify(case.expect, decision.routes, roster, decision.error)
        record = {
            "case_id": case.id, "category": case.category, "roster_size": size, "repeat": repeat,
            "expect": {"kind": case.expect.kind, "agents": list(case.expect.agents)},
            "outcome": outcome.value, "routes": decision.routes,
            "near_miss": any(is_near_miss(r, roster) for r in decision.routes),
            "steps": decision.steps, "hit_step_limit": decision.hit_step_limit,
            "duplicate_warnings": decision.duplicate_warnings,
            "prompt_tokens": decision.prompt_tokens, "completion_tokens": decision.completion_tokens,
            "cost": decision.cost, "error": decision.error, "duration_s": round(duration, 2),
            "logged_at": _now(), "raw_responses": decision.raw_responses,
        }
        async with write_lock:
            with decisions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            stats["ran"] += 1
            stats["errors"] += outcome is Outcome.ERROR
            if progress:
                print(f"  {stats['ran']:>3}/{len(jobs)}  {size:>3} agents  {case.id:<28} {outcome.value}")

    await asyncio.gather(*(one(*job) for job in jobs))
    meta["last_finished_at"] = _now()
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return stats


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- CLI --------------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> None:
    from .report import write_report

    parser = argparse.ArgumentParser(prog="python -m evals.routing", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", help="results folder name (default: 'baseline' or 'smoke', plus '-messy')")
    parser.add_argument("--variant", choices=["clean", "messy"], default="clean",
                        help="'messy' adds two duplicate decoy agents per case (see data/decoys.json)")
    parser.add_argument("--model", help="OpenRouter model id (default: the app's interaction agent model)")
    parser.add_argument("--sizes", default=",".join(map(str, ROSTER_SIZES)), help="roster sizes, comma-separated")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--smoke", action="store_true", help="2 cases per category at the two smallest sizes")
    parser.add_argument("--report-only", action="store_true", help="rebuild report.md from the log; no API calls")
    args = parser.parse_args(argv)

    settings = get_settings()
    label = args.label or ("smoke" if args.smoke else "baseline") + ("-messy" if args.variant == "messy" else "")
    out_dir = RESULTS_DIR / label
    if not args.report_only:
        sizes = tuple(int(s) for s in args.sizes.split(","))
        cases = load_cases()
        if args.smoke:
            cases, sizes = select_smoke_cases(cases), sizes[:2]
        config = RunConfig(label=label, model=args.model or settings.interaction_agent_model,
                           sizes=sizes, repeats=args.repeats, seed=args.seed, variant=args.variant)
        stats = asyncio.run(run_eval(config, cases, out_dir, api_key=settings.openrouter_api_key,
                                     concurrency=args.concurrency))
        print(f"[{label}] ran {stats['ran']} ({stats['errors']} API errors), skipped {stats['skipped']} already logged")
    print(f"[{label}] report: {write_report(out_dir)}")


if __name__ == "__main__":  # pragma: no cover
    main()
