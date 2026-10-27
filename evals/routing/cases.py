"""Data types for routing eval cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Tuple

ExpectKind = Literal["agent", "new", "none"]


@dataclass(frozen=True)
class Expectation:
    """The correct routing decision for a case.

    - kind="agent": delegate to one of `agents` (several when genuinely ambiguous)
    - kind="new":   delegate to a brand-new agent (any name not in the roster)
    - kind="none":  don't delegate at all (e.g. "thanks!")
    """

    kind: ExpectKind
    agents: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.kind == "agent") != bool(self.agents):
            raise ValueError("agents must be given exactly when kind='agent'")


@dataclass(frozen=True)
class RoutingCase:
    """One labelled routing decision.

    `required_agents` must appear in every roster this case runs against (they
    are what the history refers to); distractors are added around them.
    `history` is a sequence of (tag, text) conversation entries, oldest first,
    using the conversation log's tags: user_message, poke_reply, agent_message.
    """

    id: str
    category: str
    message: str
    expect: Expectation
    required_agents: Tuple[str, ...] = ()
    history: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
