"""Text matching between requests and agents: duplicate detection on create (P4), reused by search (P2).

Deliberately simple and explainable: compare the *meaningful* words of agent names,
ignoring generic task words ("email", "reminder", "follow up", "with", ...), so
"Alice Wong Lunch" matches "Lunch with Alice Wong" but "Email to Alice" does not
match "Lunch with Alice".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List

from ...config import get_settings
from .registry import AgentRecord

# Words that say what kind of task an agent does, not what it is about
GENERIC_WORDS = frozenset(
    """
    a an the and or of to for with about on in at by from re my me your our
    email emails mail message messages reply replies draft drafts send sent
    reminder reminders remind follow up followup task tasks agent agents new
    check request requests call calls
    """.split()
)


_WORD = re.compile(r"[a-z0-9]+")


# The meaningful words of a text: lowercased, possessives dropped, generic words removed
def meaningful_words(text: str) -> FrozenSet[str]:
    words = _WORD.findall(text.lower().replace("'s", "").replace("’s", ""))
    return frozenset(w for w in words if w not in GENERIC_WORDS)


# How alike two agent names are, 0..1: shared meaningful words over the shorter name's words.
# Needs two shared words, unless both names boil down to the same single word ("Call Mom" ~ "Mom Reminder").
def name_similarity(first: str, second: str) -> float:
    a, b = meaningful_words(first), meaningful_words(second)
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < 2 and not (a == b):
        return 0.0
    return len(shared) / min(len(a), len(b))


@dataclass(frozen=True)
class DuplicateCandidate:
    agent: AgentRecord
    similarity: float


# Existing agents a new agent called `name` would likely duplicate, most similar first
def find_possible_duplicates(name: str, agents: Iterable[AgentRecord]) -> List[DuplicateCandidate]:
    settings = get_settings()  # duplicate_name_threshold 2/3: e.g. two of three meaningful words shared
    candidates = [
        DuplicateCandidate(agent, score)
        for agent in agents
        if (score := name_similarity(name, agent.name)) >= settings.duplicate_name_threshold
    ]
    candidates.sort(key=lambda c: c.agent.last_used_at, reverse=True)  # ties: most recently used first
    candidates.sort(key=lambda c: c.similarity, reverse=True)          # stable, so recency order survives
    return candidates[:settings.duplicate_max_candidates]


__all__ = [
    "DuplicateCandidate",
    "GENERIC_WORDS",
    "find_possible_duplicates",
    "meaningful_words",
    "name_similarity",
]
