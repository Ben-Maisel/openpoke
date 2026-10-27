"""Tool definitions for interaction agent."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.execution import AgentRecord, get_agent_registry, get_execution_agent_logs
from ...services.execution.matching import DuplicateCandidate, find_possible_duplicates
from ...services.execution.search import search_agents as rank_agents
from ..execution_agent.batch_manager import ExecutionBatchManager


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
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
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

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()
# A new agent's card starts as the instructions it was created with (refreshed after each run, P1d)
_INITIAL_SUMMARY_CHARS = 500
_SUGGESTIONS = 3


# Send instructions to an existing agent by ID; an unknown ID is an error, never a silent create
def send_message_to_agent(agent_id: str, instructions: str) -> ToolResult:
    """Send instructions to an existing execution agent."""
    registry = get_agent_registry()
    agent = registry.get(agent_id)
    if agent is None:
        recent = [{"agent_id": a.id, "name": a.name} for a in registry.list_agents()[:_SUGGESTIONS]]
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
        candidates = find_possible_duplicates(name, registry.list_agents())
        if candidates:
            logger.info(f"create_agent({name!r}) held back: possible duplicate of {candidates[0].agent.id}")
            return ToolResult(success=True, payload=duplicate_warning(candidates))
    if _running_loop() is None:
        return ToolResult(success=False, payload={"error": "No event loop available"})
    agent = registry.create(name, summary=instructions[:_INITIAL_SUMMARY_CHARS])
    logger.info(f"Created agent {agent.id} ({agent.name})")
    return _dispatch(agent, instructions, new_agent_created=True)


# Look up agents beyond the ones shown in the prompt
def search_agents(query: str) -> ToolResult:
    """Search every execution agent by topic, person, or id."""
    registry = get_agent_registry()
    return ToolResult(success=True, payload=agent_search_results(query, registry.list_agents(), registry.all_facts()))


# What search_agents tells the model (shared with the routing eval)
def agent_search_results(query: str, agents: List[AgentRecord], facts_by_id: Dict[str, Any]) -> Dict[str, Any]:
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

    get_execution_agent_logs().record_request(agent.id, instructions)

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


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    return ToolResult(
        success=True,
        payload={
            "status": "draft_recorded",
            "to": to,
            "subject": subject,
        },
        recorded_reply=True,
    )


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
