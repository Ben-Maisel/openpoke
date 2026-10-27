"""Tool definitions for interaction agent."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Optional

from ...config import get_settings
from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.execution import AgentRecord, get_agent_registry, get_draft_store
from ...services.execution.lifecycle import split_archived
from ...services.execution.matching import DuplicateCandidate, find_possible_duplicates
from ...services.execution.search import search_agents as rank_agents
from ..execution_agent.batch_manager import get_execution_batch_manager


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for OpenRouter
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to an existing execution agent, identified by its id from <active_agents>. The agent keeps its full history, so use this for anything continuing earlier work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The agent's id exactly as listed in <active_agents> (e.g., 'a17').",
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["agent_id", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_agent",
            "description": "Start a new execution agent for a task no existing agent covers, and give it its first instructions. If it looks like an existing agent, nothing is created and the likely matches are returned instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short, specific name for the task (e.g., 'Vercel Job Offer', 'Lunch with Alice Wong').",
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                    "confirm_new": {
                        "type": "boolean",
                        "description": "Only after a possible-duplicate response: true if this really is a separate task.",
                    },
                },
                "required": ["name", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_agents",
            "description": "Search all existing execution agents, including ones not shown in <active_agents>, by topic, person, or id. Returns the best matches with their cards. Use it before create_agent when an older agent might already cover the task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Who or what to look for (e.g., 'landlord lease renewal')."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": (
                "Show the user an email draft that an agent created, by its draft ID. The exact stored text "
                "(recipients, subject, body) is shown; never write or retype email text yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The draft ID from the agent's report, e.g. 'r-12'.",
                    },
                },
                "required": ["draft_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_draft",
            "description": (
                "Record that the user approved a draft they were shown, when their latest message clearly says to "
                "send it. Only approved drafts can be sent. Call this before asking the agent to send the draft."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "The ID of the draft the user approved, e.g. 'r-12'.",
                    },
                },
                "required": ["draft_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

_EXECUTION_BATCH_MANAGER = get_execution_batch_manager()


# Send instructions to an existing agent by ID; an unknown ID is an error, never a silent create
def send_message_to_agent(agent_id: str, instructions: str) -> ToolResult:
    """Send instructions to an existing execution agent."""
    registry = get_agent_registry()
    agent = registry.get(agent_id)
    if agent is None:
        active, _ = split_archived(registry.list_agents(), registry.all_facts())
        recent = [{"agent_id": a.id, "name": a.name} for a in active[:get_settings().unknown_agent_suggestions]]
        return ToolResult(success=False, payload={
            "error": f"No agent with id {agent_id!r}. Use an id from <active_agents>, or create_agent for a new task.",
            "recently_used_agents": recent,
        })
    return _dispatch(agent, instructions, new_agent_created=False)


# Start a new agent, unless it looks like one that already exists (then return the likely matches)
def create_agent(name: str, instructions: str, confirm_new: bool = False) -> ToolResult:
    """Create a new execution agent and send it its first instructions."""
    registry = get_agent_registry()
    if not confirm_new:
        active, _ = split_archived(registry.list_agents(), registry.all_facts())  # archived agents aren't duplicates
        candidates = find_possible_duplicates(name, active)
        if candidates:
            logger.info(f"create_agent({name!r}) held back: possible duplicate of {candidates[0].agent.id}")
            return ToolResult(success=True, payload=duplicate_warning(candidates))
    if _running_loop() is None:
        return ToolResult(success=False, payload={"error": "No event loop available"})
    # A new agent's card starts as the instructions it was created with (refreshed after each run, P1d)
    agent = registry.create(name, summary=instructions[:get_settings().initial_card_chars])
    logger.info(f"Created agent {agent.id} ({agent.name})")
    return _dispatch(agent, instructions, new_agent_created=True)


# Look up agents beyond the ones shown in the prompt
def search_agents(query: str) -> ToolResult:
    """Search every execution agent by topic, person, or id."""
    registry = get_agent_registry()
    agents, facts = registry.list_agents(), registry.all_facts()
    _, archived = split_archived(agents, facts)
    return ToolResult(success=True, payload=agent_search_results(query, agents, facts, {a.id for a in archived}))


# What search_agents tells the model (shared with the routing eval)
def agent_search_results(
    query: str, agents: List[AgentRecord], facts_by_id: Dict[str, Any], archived_ids: Collection[str] = (),
) -> Dict[str, Any]:
    matches = rank_agents(query, agents, facts_by_id)
    if not matches:
        return {"agents": [], "total_agents": len(agents),
                "note": "No matching agents. If this is a new task, use create_agent."}
    return {
        "agents": [
            {
                "agent_id": m.agent.id,
                "name": m.agent.name,
                "summary": m.agent.summary,
                "last_used": m.agent.last_used_at,
                "open_drafts": facts_by_id.get(m.agent.id, {}).get("draft", []),
                "live_reminders": facts_by_id.get(m.agent.id, {}).get("trigger", []),
                **({"archived": True} if m.agent.id in archived_ids else {}),
            }
            for m in matches
        ],
        "total_agents": len(agents),
    }


# What create_agent tells the model instead of creating a likely duplicate (shared with the routing eval)
def duplicate_warning(candidates: List[DuplicateCandidate]) -> Dict[str, Any]:
    return {
        "status": "possible_duplicate",
        "created": False,
        "similar_agents": [
            {"agent_id": c.agent.id, "name": c.agent.name, "summary": c.agent.summary} for c in candidates
        ],
        "next_step": "If one of these is the same task, use send_message_to_agent with its agent_id. "
                     "If this really is separate, call create_agent again with confirm_new=true.",
    }


# Log the request and run the agent in the background; the reply says which agent took it
def _dispatch(agent: AgentRecord, instructions: str, *, new_agent_created: bool) -> ToolResult:
    loop = _running_loop()
    if loop is None:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(agent.id, instructions)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent {agent.id} ({agent.name}) completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Agent {agent.id} ({agent.name}) failed: {str(exc)}")

    loop.create_task(_execute_async())
    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "new_agent_created": new_agent_created,
        },
    )


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Show the user a stored draft by ID, exactly as it will be sent
def send_draft(draft_id: str) -> ToolResult:
    """Record a stored draft in the conversation log as a draft bubble and mark it shown."""
    store = get_draft_store()
    draft = store.get(draft_id) if isinstance(draft_id, str) else None
    if draft is None or draft.status in ("sent", "deleted"):
        reason = f"Draft {draft_id!r} is {draft.status}." if draft else f"No draft {draft_id!r} on record."
        showable = [d.id for d in store.list_drafts(statuses=("open", "shown", "approved"))[:10]]
        return ToolResult(
            success=False,
            payload={"error": f"{reason} Ask the agent that wrote it for the draft ID.", "showable_drafts": showable},
        )

    store.mark_shown(draft.id)
    get_conversation_log().record_draft(f"Draft {draft.id}\n{draft.render()}")
    logger.info(f"Draft {draft.id} shown to the user")

    return ToolResult(
        success=True,
        payload={"status": "draft_shown", "draft_id": draft.id, "to": draft.recipient, "subject": draft.subject},
        recorded_reply=True,
    )


# Record the user's approval of a draft they were shown; only approved drafts can be sent (P20)
def approve_draft(draft_id: str) -> ToolResult:
    """Approve a shown draft, citing the user's reply that came after it as the evidence."""
    store = get_draft_store()
    draft = store.get(draft_id) if isinstance(draft_id, str) else None
    if draft is None or draft.status not in ("shown", "approved"):
        state = f"is {draft.status}" if draft else "is not on record"
        return ToolResult(success=False, payload={
            "error": f"Draft {draft_id!r} {state}. Only a draft shown to the user with send_draft can be approved.",
        })

    # The approval must be a user message that arrived after this draft was last shown
    reply_after_showing: Optional[str] = None
    shown_in_chat = False
    for tag, _, payload in get_conversation_log().iter_entries():
        if tag == "draft" and payload.startswith(f"Draft {draft.id}\n"):
            shown_in_chat, reply_after_showing = True, None
        elif tag == "user_message" and shown_in_chat:
            reply_after_showing = payload
    if reply_after_showing is None:
        return ToolResult(success=False, payload={
            "error": f"The user hasn't replied since seeing draft {draft.id}. Wait for their answer before approving it.",
        })

    store.approve(draft.id, reply_after_showing)
    logger.info(f"Draft {draft.id} approved by the user")
    return ToolResult(success=True, payload={
        "status": "approved", "draft_id": draft.id, "approved_by_user_message": reply_after_showing,
    })


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()
    
    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)
    

    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


# Return predefined tool schemas for LLM function calling
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas."""
    return TOOL_SCHEMAS


# Route tool calls to appropriate handlers with argument validation and error handling
def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if isinstance(arguments, str):
            args = json.loads(arguments) if arguments.strip() else {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "send_message_to_agent":
            return send_message_to_agent(**args)
        if name == "create_agent":
            return create_agent(**args)
        if name == "search_agents":
            return search_agents(**args)
        if name == "send_message_to_user":
            return send_message_to_user(**args)
        if name == "send_draft":
            return send_draft(**args)
        if name == "approve_draft":
            return approve_draft(**args)
        if name == "wait":
            return wait(**args)

        logger.warning("unexpected tool", extra={"tool": name})
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except json.JSONDecodeError:
        return ToolResult(success=False, payload={"error": "Invalid JSON"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tool call failed", extra={"tool": name, "error": str(exc)})
        return ToolResult(success=False, payload={"error": "Failed to execute"})
