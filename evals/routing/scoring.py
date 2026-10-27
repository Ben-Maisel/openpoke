"""Deterministic scoring for routing decisions: no LLM judge needed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

from server.services.execution.log_store import _slugify

from .cases import Expectation


class Outcome(str, Enum):
    CORRECT = "correct"
    WRONG_AGENT = "wrong_agent"                  # routed to an existing agent that isn't right (worst: mixes context)
    DUPLICATE = "duplicate"                      # created a new agent when an existing one should be reused
    WRONGLY_REUSED = "wrongly_reused"            # reused an existing agent when a new one was needed
    NO_DELEGATION = "no_delegation"              # should have delegated, didn't
    UNNEEDED_DELEGATION = "unneeded_delegation"  # delegated when it should just have replied
    ERROR = "error"                              # the API call failed; excluded from accuracy


# Classify where the model routed, judged the way the app does: exact name match against the roster
def classify(expect: Expectation, routes: Sequence[str], roster: Sequence[str], error: Optional[str] = None) -> Outcome:
    if error:
        return Outcome.ERROR
    existing = set(roster)
    hits_existing = [r for r in routes if r in existing]

    if expect.kind == "none":
        return Outcome.CORRECT if not routes else Outcome.UNNEEDED_DELEGATION
    if not routes:
        return Outcome.NO_DELEGATION
    if expect.kind == "new":
        return Outcome.WRONGLY_REUSED if hits_existing else Outcome.CORRECT

    acceptable = set(expect.agents)
    if any(r not in acceptable for r in hits_existing):
        return Outcome.WRONG_AGENT
    if hits_existing:
        return Outcome.CORRECT
    return Outcome.DUPLICATE


# A route that isn't an exact roster name but collides with one after case/punctuation folding (see P4)
def is_near_miss(route: str, roster: Sequence[str]) -> bool:
    if route in roster:
        return False
    return any(_slugify(route) == _slugify(name) for name in roster)


# 95% Wilson score interval for a proportion; well-behaved for small n and rates near 0 or 1
def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class CaseResult:
    case_id: str
    category: str
    roster_size: int
    outcome: Outcome
    routes: List[str]
    near_miss: bool
    prompt_tokens: int
    completion_tokens: int
    cost: float
    repeat: int = 0


@dataclass
class GroupSummary:
    n: int                       # scored results (errors excluded)
    errors: int
    accuracy: float
    ci_low: float
    ci_high: float
    outcomes: Dict[str, int]
    near_misses: int
    mean_prompt_tokens: float
    total_cost: float


# Aggregate results into one summary per group (e.g. per roster size)
def summarize(results: Iterable[CaseResult], key: Callable[[CaseResult], Hashable]) -> Dict[Hashable, GroupSummary]:
    groups: Dict[Hashable, List[CaseResult]] = {}
    for result in results:
        groups.setdefault(key(result), []).append(result)

    summaries = {}
    for group_key, items in sorted(groups.items(), key=lambda kv: kv[0]):
        scored = [r for r in items if r.outcome is not Outcome.ERROR]
        correct = sum(r.outcome is Outcome.CORRECT for r in scored)
        low, high = wilson_interval(correct, len(scored))
        summaries[group_key] = GroupSummary(
            n=len(scored),
            errors=len(items) - len(scored),
            accuracy=correct / len(scored) if scored else 0.0,
            ci_low=low,
            ci_high=high,
            outcomes={o.value: sum(r.outcome is o for r in items) for o in Outcome},
            near_misses=sum(r.near_miss for r in items),
            mean_prompt_tokens=sum(r.prompt_tokens for r in scored) / len(scored) if scored else 0.0,
            total_cost=sum(r.cost for r in items),
        )
    return summaries
