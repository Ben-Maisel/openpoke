"""Interaction agent helpers for prompt construction."""

import re
from html import escape
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ...services.execution import AgentRecord, get_agent_registry
from ...services.execution.lifecycle import split_archived
from ...services.execution.search import WorkingSet, select_working_set

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()

# How much of the end of the conversation feeds the agent search, so a follow-up like
# "what did she say?" still has the names and topics it refers to (about the last two turns).
_RECENT_CONTEXT_CHARS = 600
_TAG = re.compile(r"<[^>]+>")

FactsById = Mapping[str, Mapping[str, Sequence[str]]]


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Return the static system prompt for the interaction agent."""
    return SYSTEM_PROMPT


# The text the agent search runs on: the new message plus the tail of the conversation
def routing_query(latest_text: str, transcript: str) -> str:
    recent = _TAG.sub(" ", transcript)[-_RECENT_CONTEXT_CHARS:]
    return f"{latest_text}\n{recent}"


# The live working set: recently used agents plus the best matches for this turn (P2), archived ones left out (P3)
def build_working_set(latest_text: str, transcript: str) -> Tuple[WorkingSet, Dict[str, Dict[str, List[str]]]]:
    registry = get_agent_registry()
    facts = registry.all_facts()
    active, archived = split_archived(registry.list_agents(), facts)
    working_set = select_working_set(routing_query(latest_text, transcript), active, facts)
    working_set.archived = len(archived)
    return working_set, facts


# Build structured message with conversation history, the agents in view, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
    working_set: Optional[WorkingSet] = None,
    facts_by_id: Optional[FactsById] = None,
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, the agent working set, and the latest turn.

    `working_set` (and its agents' facts) can be supplied, as the routing eval does;
    by default it is built from the live registry.
    """
    if working_set is None:
        working_set, facts_by_id = build_working_set(latest_text, transcript)

    sections: List[str] = []
    sections.append(_render_conversation_history(transcript))
    sections.append(f"<active_agents>\n{render_working_set(working_set, facts_by_id or {})}\n</active_agents>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Format the working set: recent agents and matching agents with their cards, plus a count of the rest
def render_working_set(working_set: WorkingSet, facts_by_id: FactsById) -> str:
    if working_set.total_agents == 0 and not working_set.archived:
        return "None"

    sections: List[str] = []
    if working_set.hot:
        cards = "\n".join(_render_card(agent, facts_by_id.get(agent.id, {})) for agent in working_set.hot)
        sections.append(f"<recent_agents>\n{cards}\n</recent_agents>")
    if working_set.matches:
        cards = "\n".join(_render_card(m.agent, facts_by_id.get(m.agent.id, {})) for m in working_set.matches)
        sections.append(f"<matching_agents>\n{cards}\n</matching_agents>")
    hidden = working_set.total_agents - len(working_set.agents)
    if hidden > 0:
        sections.append(
            f'<other_agents count="{hidden}">Not shown. Use search_agents to look for one before creating a new agent.</other_agents>'
        )
    if working_set.archived:
        sections.append(
            f'<archived_agents count="{working_set.archived}">Idle for a while with nothing open, so not shown. '
            'search_agents still finds them; using one makes it active again.</archived_agents>'
        )
    return "\n".join(sections)


# One agent card: id, name, when it was last used, its summary, and anything still open
def _render_card(agent: AgentRecord, facts: Mapping[str, Sequence[str]]) -> str:
    details = [agent.summary.strip()] if agent.summary.strip() else []
    if facts.get("draft"):
        details.append(f"Open drafts: {', '.join(facts['draft'])}.")
    if facts.get("trigger"):
        details.append(f"Live reminders: {', '.join(facts['trigger'])}.")
    last_used = agent.last_used_at.replace("T", " ")[:16]
    attributes = f'id="{escape(agent.id, quote=True)}" name="{escape(agent.name or "agent", quote=True)}"'
    if last_used:
        attributes += f' last_used="{escape(last_used, quote=True)}"'
    return f"<agent {attributes}>{escape(' '.join(details), quote=False)}</agent>"


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
