"""Urgency eval (P7): does the real email classifier interrupt the user for the right emails?

Each labelled email goes through the app's own `classify_email_importance`, and its verdict is
turned into the lane the watcher would use: urgent -> now, important -> digest, otherwise none.

Results live in evals/results/<label>/ (meta.json, decisions.jsonl, report.md). Re-running the same
label resumes: logged answers are skipped and only API errors are retried.

    python -m evals.notifications.urgency [--repeats N] [--label NAME] [--report-only]
"""

from __future__ import annotations

import os
import tempfile

# Importing the server creates its stores; keep a command-line run away from server/data
os.environ.setdefault("OPENPOKE_DATA_DIR", tempfile.mkdtemp(prefix="openpoke-eval-data-"))

import argparse  # noqa: E402
import asyncio
import hashlib
import json
import subprocess
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from server.config import get_settings
from server.services.gmail import importance_classifier as classifier_module
from server.services.gmail.importance_classifier import classify_email_importance

from ..routing.scoring import wilson_interval
from .dataset import DATA_DIR, EXPECTED_LANE, LabelledEmail, load_emails

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
_raw: ContextVar[Optional[Dict[str, Any]]] = ContextVar("urgency_raw", default=None)
Key = Tuple[str, int]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_identity(model: str) -> Dict[str, str]:
    prompt = classifier_module._SYSTEM_PROMPT + json.dumps(classifier_module._TOOL_SCHEMA, sort_keys=True)
    return {"model": model, "dataset_sha": _sha((DATA_DIR / "emails.json").read_text(encoding="utf-8")),
            "classifier_sha": _sha(prompt)}


# The classifier hides API errors (it returns None, the same as "not important"), so record them here
async def _recording_completion(**kwargs: Any) -> Dict[str, Any]:
    record = _raw.get()
    try:
        response = await _real_completion(**kwargs)
    except Exception as exc:
        if record is not None:
            record["error"] = str(exc)
        raise
    if record is not None:
        record["response"] = response
    return response


_real_completion: Any = None  # the classifier's own completion function, captured when a run starts


def lane_of(verdict: Any) -> Optional[str]:
    if verdict is None:
        return None
    return "now" if verdict.urgent else "digest"


async def judge(email: LabelledEmail) -> Dict[str, Any]:
    record: Dict[str, Any] = {}
    token = _raw.set(record)
    try:
        verdict = await classify_email_importance(email.as_processed())
    finally:
        _raw.reset(token)
    usage = (record.get("response") or {}).get("usage") or {}
    return {
        "lane": lane_of(verdict), "summary": verdict.summary if verdict else None, "error": record.get("error"),
        "prompt_tokens": usage.get("prompt_tokens", 0), "cost": usage.get("cost", 0.0) or 0.0,
        "raw_response": record.get("response"),
    }


def read_decisions(path: Path) -> Dict[Key, Dict[str, Any]]:
    latest: Dict[Key, Dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                key = (record["email_id"], record["repeat"])
                if key not in latest or latest[key]["error"] or not record["error"]:
                    latest[key] = record
    return latest


async def run_eval(label: str, model: str, emails: Sequence[LabelledEmail], repeats: int, out_dir: Path,
                   *, progress: bool = True) -> Dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    identity = run_identity(model)
    meta_path, decisions_path = out_dir / "meta.json", out_dir / "decisions.jsonl"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
    if meta and meta["identity"] != identity:
        raise RuntimeError(f"{out_dir} holds a different run ({meta['identity']}); use a new --label.")
    meta = meta or {"label": label, "identity": identity}
    meta.update({"repeats": repeats, "git_commit": _git("rev-parse", "HEAD"),
                 "git_dirty": bool(_git("status", "--porcelain"))})
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    done = {k for k, r in read_decisions(decisions_path).items() if not r["error"]}
    jobs = [(e, rep) for e in emails for rep in range(repeats) if (e.id, rep) not in done]
    stats = {"ran": 0, "errors": 0, "skipped": len(emails) * repeats - len(jobs)}
    global _real_completion
    _real_completion = classifier_module.request_chat_completion
    classifier_module.request_chat_completion = _recording_completion
    try:
        for email, repeat in jobs:  # sequential: 24 short calls, no need to rush the API
            result = await judge(email)
            record = {"email_id": email.id, "label": email.label, "repeat": repeat,
                      "expected_lane": EXPECTED_LANE[email.label], **result,
                      "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            with decisions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            stats["ran"] += 1
            stats["errors"] += bool(result["error"])
            if progress:
                print(f"  {email.id:<4} {email.label:<14} -> {result['lane'] or 'none':<7} {'ERROR' if result['error'] else ''}")
    finally:
        classifier_module.request_chat_completion = _real_completion
    return stats


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def write_report(out_dir: Path) -> Path:
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    records = [r for r in read_decisions(out_dir / "decisions.jsonl").values() if not r["error"]]
    emails = {e.id: e for e in load_emails()}
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        by_label.setdefault(r["label"], []).append(r)

    def rate(label: str, test) -> Tuple[int, int]:
        rows = by_label.get(label, [])
        return sum(test(r) for r in rows), len(rows)

    def cell(hits: int, n: int) -> str:
        low, high = wilson_interval(hits, n)
        return f"**{_pct(hits / n)}** ({hits}/{n}, CI {_pct(low)}–{_pct(high)})" if n else "–"

    missed = rate("urgent", lambda r: r["lane"] != "now")
    false_interrupts = (sum(r["lane"] == "now" for r in records if r["label"] != "urgent"),
                        sum(r["label"] != "urgent" for r in records))
    dropped = rate("important", lambda r: r["lane"] is None)
    exact = sum(r["lane"] == r["expected_lane"] for r in records)
    identity = meta["identity"]
    lines = [
        f"# Urgency eval: `{meta['label']}`",
        "",
        "The app's own email classifier judges 24 labelled emails (8 urgent, 8 important, 8 not important). "
        "Its verdict decides the lane: urgent → now (interrupts), important → digest, otherwise nothing.",
        "",
        f"- **Model:** `{identity['model']}` · **dataset** `{identity['dataset_sha']}` · **classifier prompt** "
        f"`{identity['classifier_sha']}` · **code** `{meta.get('git_commit', 'unknown')[:10]}`",
        f"- **Judgements:** {len(records)} · **cost:** ${sum(r['cost'] for r in records):.4f}",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Exact lane | {cell(exact, len(records))} |",
        f"| Missed urgent (urgent email didn't interrupt) | {cell(*missed)} |",
        f"| False interrupts (non-urgent email interrupted) | {cell(*false_interrupts)} |",
        f"| Dropped (important email not surfaced at all) | {cell(*dropped)} |",
        "",
        "## Where each label went",
        "",
        "| Label | now | digest | none |",
        "|---|---:|---:|---:|",
    ]
    for label in ("urgent", "important", "not_important"):
        counts = Counter(r["lane"] for r in by_label.get(label, []))
        lines.append(f"| {label.replace('_', ' ')} | {counts['now']} | {counts['digest']} | {counts[None]} |")
    mistakes = sorted((r for r in records if r["lane"] != r["expected_lane"]), key=lambda r: r["email_id"])
    lines += ["", f"## Every mistake ({len(mistakes)})", ""]
    if mistakes:
        lines += ["| Email | Label | Expected | Got | Subject | Classifier's summary |", "|---|---|---|---|---|---|"]
        for r in mistakes:
            summary = " ".join((r["summary"] or "").split()).replace("|", "/")
            lines.append(f"| {r['email_id']} | {r['label']} | {r['expected_lane'] or 'none'} | {r['lane'] or 'none'} "
                         f"| {emails[r['email_id']].subject} | {summary} |")
    else:
        lines.append("None.")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[Sequence[str]] = None) -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(prog="python -m evals.notifications.urgency", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="notifications-urgency")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    out_dir = RESULTS_DIR / args.label
    if not args.report_only:
        settings = get_settings()
        stats = asyncio.run(run_eval(args.label, settings.email_classifier_model, load_emails(), args.repeats, out_dir))
        print(f"[{args.label}] ran {stats['ran']} ({stats['errors']} API errors), skipped {stats['skipped']}")
    print(f"[{args.label}] report: {write_report(out_dir)}")


if __name__ == "__main__":  # pragma: no cover
    main()
