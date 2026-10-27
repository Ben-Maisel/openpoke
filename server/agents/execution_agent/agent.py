"""Execution Agent implementation."""

from pathlib import Path
from typing import List, Optional, Dict, Any

from ...services.execution import get_execution_agent_logs
from .memory import render_history
from ...logging_config import logger


# Load system prompt template from file
_prompt_path = Path(__file__).parent / "system_prompt.md"
if _prompt_path.exists():
    SYSTEM_PROMPT_TEMPLATE = _prompt_path.read_text(encoding="utf-8").strip()
else:
    # Placeholder template - you'll replace this with actual instructions
    SYSTEM_PROMPT_TEMPLATE = """You are an execution agent responsible for completing specific tasks using available tools.

Agent Name: {agent_name}
Purpose: {agent_purpose}

Instructions:
[TO BE FILLED IN BY USER]

You have access to Gmail tools to help complete your tasks. When given instructions:
1. Analyze what needs to be done
2. Use the appropriate tools to complete the task
3. Provide clear status updates on your actions

Be thorough, accurate, and efficient in your execution."""


class ExecutionAgent:
    """Manages state and history for an execution agent."""

    # Initialize execution agent with its ID, display name, conversation limits, and log store access
    def __init__(
        self,
        agent_id: str,
        name: str,
        summary: str = "",
        conversation_limit: Optional[int] = None
    ):
        """
        Initialize an execution agent.

        Args:
            agent_id: Stable registry ID (e.g., 'a17'); memory is stored under it
            name: Human-readable agent name (e.g., 'conversation with keith')
            summary: The agent's card summary (what it handles and where things stand)
            conversation_limit: Optional limit on past conversations to include (None = all)
        """
        self.agent_id = agent_id
        self.name = name
        self.summary = summary
        self.conversation_limit = conversation_limit
        self._log_store = get_execution_agent_logs()

    # Generate system prompt template with the agent's name and its card summary as its purpose
    def build_system_prompt(self) -> str:
        """Build the system prompt for this agent."""
        agent_purpose = self.summary or f"Handle tasks related to: {self.name}"

        return SYSTEM_PROMPT_TEMPLATE.format(
            agent_name=self.name,
            agent_purpose=agent_purpose
        )

    # Combine base system prompt with conversation history, applying conversation limits
    def build_system_prompt_with_history(self) -> str:
        """
        Build system prompt including agent history.

        Returns:
            System prompt with embedded history transcript
        """
        base_prompt = self.build_system_prompt()

        entries = list(self._log_store.iter_entries(self.agent_id))

        if entries:
            # Apply conversation limit if needed: keep the last N requests and everything after them
            if self.conversation_limit and self.conversation_limit > 0:
                starts = [i for i, (tag, _, _) in enumerate(entries) if tag == "agent_request"]
                if len(starts) > self.conversation_limit:
                    entries = entries[starts[-self.conversation_limit]:]

            return f"{base_prompt}\n\n# Execution History\n\n{render_history(entries)}"

        return base_prompt

    # Format current instruction as user message for LLM consumption
    def build_messages_for_llm(self, current_instruction: str) -> List[Dict[str, str]]:
        """
        Build message array for LLM call.

        Args:
            current_instruction: Current instruction from interaction agent

        Returns:
            List of messages in OpenRouter format
        """
        return [
            {"role": "user", "content": current_instruction}
        ]

    # Log the agent's final response to the execution log store
    def record_response(self, response: str) -> None:
        """Record agent's response to the log."""
        self._log_store.record_agent_response(self.agent_id, response)

    # Log a tool call in full; the prompt later shows a digest of it
    def record_tool_execution(self, tool_name: str, arguments: Dict[str, Any], success: bool, result: Any) -> str:
        """Record a tool call and its result in full, returning its reference (e.g. 'r3')."""
        return self._log_store.record_tool_call(self.agent_id, tool_name, arguments, success, result)
