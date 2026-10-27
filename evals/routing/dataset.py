"""Load the labelled cases and build seeded, nested rosters of distractor agents around them."""

from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .cases import Expectation, RoutingCase

DATA_DIR = Path(__file__).parent / "data"
ROSTER_SIZES = (10, 50, 200, 500)
DEFAULT_SEED = 20261026


def load_cases(path: Path = DATA_DIR / "cases.json") -> List[RoutingCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))["cases"]
    return [
        RoutingCase(
            id=item["id"],
            category=item["category"],
            message=item["message"],
            expect=Expectation(item["expect"]["kind"], tuple(item["expect"].get("agents", ()))),
            required_agents=tuple(item.get("required_agents", ())),
            history=tuple((tag, text) for tag, text in item.get("history", ())),
            exclude=tuple(item.get("exclude", ())),
        )
        for item in raw
    ]


def load_world(path: Path = DATA_DIR / "world.json") -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# Every distinct agent name the world's templates can produce, sorted for determinism
@lru_cache(maxsize=1)
def distractor_pool() -> List[str]:
    world = load_world()
    t = world["templates"]
    names = set()
    for full in world["people"]:
        first = full.split()[0]
        names.update(x.format(first=first) for x in t["person_first"])
        names.update(x.format(first=first, full=full) for x in t["person_full"])
        names.update(x.format(topic=topic, first=first) for x in t["work_topic"] for topic in world["work_topics"])
    for company in world["companies"]:
        names.update(x.format(company=company, thing=thing) for x in t["company"] for thing in world["company_things"])
    names.update(x.format(reminder=r) for x in t["reminder"] for r in world["personal_reminders"])
    names.update(x.format(family=f) for x in t["family"] for f in world["family"])
    names.update(x.format(city=c) for x in t["city"] for c in world["cities"])
    return sorted(names)


# True if any term starts a word in `name` (case-insensitive); over-matching only shrinks the pool
def mentions(name: str, terms: Sequence[str]) -> bool:
    return any(re.search(r"\b" + re.escape(term.lower()), name.lower()) for term in terms)


# The case's required agents plus seeded distractors, `size` agents in total.
# The same seed gives the same shuffle at every size, so smaller rosters are subsets of larger ones,
# and each required agent sits at the same relative position (fraction of the roster) at every size.
def build_roster(case: RoutingCase, size: int, seed: int = DEFAULT_SEED) -> List[str]:
    rng = random.Random(f"{seed}:{case.id}")
    candidates = [n for n in distractor_pool() if n not in case.required_agents and not mentions(n, case.exclude)]
    rng.shuffle(candidates)
    needed = size - len(case.required_agents)
    if needed < 0 or needed > len(candidates):
        raise ValueError(f"{case.id}: cannot build a roster of {size} from {len(candidates)} candidates")

    roster = candidates[:needed]
    positions = [rng.random() for _ in case.required_agents]
    for agent, fraction in zip(case.required_agents, positions):
        roster.insert(round(fraction * len(roster)), agent)
    return roster
