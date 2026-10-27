"""Find the agents relevant to a request, and build the working set the interaction agent sees (P2).

The working set is the hot cache (most recently used agents, which covers follow-ups like
"send it") plus the best search matches for the request (which covers older agents).

Search is lexical and explainable: an agent scores for each meaningful word of the request
found in its name (strongest), its card facts (people, email addresses, IDs), or its card
summary, each weighted by how rare the word is across all agents (IDF), so a word shared by
many agents ("alice") counts for less than a distinctive one ("nopa").
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence

from .matching import meaningful_words
from .registry import AgentRecord

NAME_WEIGHT = 3.0
FACT_WEIGHT = 2.0
SUMMARY_WEIGHT = 1.0
ID_MENTION_SCORE = 100.0  # a request quoting an agent's id ("... (a17)") is about that agent

HOT_CACHE_SIZE = 5
SHORTLIST_SIZE = 5

_AGENT_ID = re.compile(r"\ba(\d+)\b")


@dataclass(frozen=True)
class ScoredAgent:
    agent: AgentRecord
    score: float


@dataclass
class WorkingSet:
    """What the interaction agent is shown: recent agents, then the best matches not already shown."""

    hot: List[AgentRecord] = field(default_factory=list)
    matches: List[ScoredAgent] = field(default_factory=list)
    total_agents: int = 0
    archived: int = 0  # agents left out entirely because they're archived (P3)

    @property
    def agents(self) -> List[AgentRecord]:
        return [*self.hot, *(m.agent for m in self.matches)]


@dataclass(frozen=True)
class _Profile:
    name: FrozenSet[str]
    facts: FrozenSet[str]
    summary: FrozenSet[str]

    @property
    def all_words(self) -> FrozenSet[str]:
        return self.name | self.facts | self.summary


def _profile(agent: AgentRecord, facts: Mapping[str, Sequence[str]]) -> _Profile:
    fact_text = " ".join(
        value.replace(".", " ").replace("@", " ").replace("_", " ")  # alice.wong@acme.com -> alice wong acme com
        for kind in ("person", "email", "thread", "draft", "trigger")
        for value in facts.get(kind, ())
    )
    return _Profile(meaningful_words(agent.name), meaningful_words(fact_text), meaningful_words(agent.summary))


# Agents ranked by relevance to `query`, best first; agents that match nothing are left out
def search_agents(
    query: str,
    agents: Sequence[AgentRecord],
    facts_by_id: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    limit: int = SHORTLIST_SIZE,
) -> List[ScoredAgent]:
    facts_by_id = facts_by_id or {}
    query_words = meaningful_words(query)
    mentioned_ids = {f"a{n}" for n in _AGENT_ID.findall(query.lower())}
    if not agents or not (query_words or mentioned_ids):
        return []

    profiles = {agent.id: _profile(agent, facts_by_id.get(agent.id, {})) for agent in agents}
    document_frequency: Dict[str, int] = {}
    for profile in profiles.values():
        for word in profile.all_words & query_words:
            document_frequency[word] = document_frequency.get(word, 0) + 1
    total = len(agents)

    scored = []
    for agent in agents:
        profile = profiles[agent.id]
        score = ID_MENTION_SCORE if agent.id in mentioned_ids else 0.0
        for word in query_words:
            weight = (NAME_WEIGHT if word in profile.name else 0.0) + (FACT_WEIGHT if word in profile.facts else 0.0) \
                + (SUMMARY_WEIGHT if word in profile.summary else 0.0)
            if weight:
                score += weight * math.log(1 + total / document_frequency[word])
        if score > 0:
            scored.append(ScoredAgent(agent, score))

    scored.sort(key=lambda s: s.agent.last_used_at, reverse=True)  # ties: most recently used first
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:limit]


# The hot cache (most recently used) plus the best search matches that aren't already in it
def select_working_set(
    query: str,
    agents: Sequence[AgentRecord],
    facts_by_id: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None,
    hot_size: int = HOT_CACHE_SIZE,
    shortlist_size: int = SHORTLIST_SIZE,
) -> WorkingSet:
    by_recency = sorted(agents, key=lambda a: (a.last_used_at, int(a.id[1:])), reverse=True)
    hot = by_recency[:hot_size]
    hot_ids = {a.id for a in hot}
    candidates = [a for a in agents if a.id not in hot_ids]
    matches = search_agents(query, candidates, facts_by_id, limit=shortlist_size)
    return WorkingSet(hot=hot, matches=matches, total_agents=len(agents))


__all__ = ["HOT_CACHE_SIZE", "SHORTLIST_SIZE", "ScoredAgent", "WorkingSet", "search_agents", "select_working_set"]
