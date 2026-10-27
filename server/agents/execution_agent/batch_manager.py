"""Coordinate execution agents and batch their results for the interaction agent."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .runtime import ExecutionAgentRuntime, ExecutionResult
from ...config import get_settings
from ...logging_config import logger
from ...services.execution import get_agent_registry, get_execution_agent_logs


# The agent's name for reports, falling back to its ID if the agent no longer exists
def _display_name(agent_id: str) -> str:
    record = get_agent_registry().get(agent_id)
    return record.name if record else agent_id


@dataclass
class PendingExecution:
    """Track a pending execution request."""

    request_id: str
    agent_id: str
    instructions: str
    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class _BatchState:
    """Collect results for a single interaction-agent turn."""

    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)
    pending: int = 0
    results: List[ExecutionResult] = field(default_factory=list)


class ExecutionBatchManager:
    """Run execution agents and deliver their combined outcome."""

    # Initialize batch manager with timeout and coordination state for execution agents
    def __init__(self, timeout_seconds: Optional[int] = None) -> None:
        self.timeout_seconds = get_settings().execution_timeout_seconds if timeout_seconds is None else timeout_seconds
        self._pending: Dict[str, PendingExecution] = {}
        self._batch_lock = asyncio.Lock()
        self._batch_state: Optional[_BatchState] = None
        self._agent_locks: Dict[str, asyncio.Lock] = {}  # one run at a time per agent (P10)

    # Run execution agent with timeout handling and batch coordination for interaction agent
    async def execute_agent(
        self,
        agent_id: str,
        instructions: str,
        request_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute an agent asynchronously and buffer the result for batch dispatch."""

        if not request_id:
            request_id = str(uuid.uuid4())

        batch_id = await self._register_pending_execution(agent_id, instructions, request_id)

        try:
            # Runs of the same agent wait their turn, so each one starts from the previous run's memory.
            # The request is journaled once the run starts, keeping each request next to its own result.
            async with self._agent_locks.setdefault(agent_id, asyncio.Lock()):
                logger.info(f"[{agent_id}] Execution started")
                get_execution_agent_logs().record_request(agent_id, instructions)
                runtime = ExecutionAgentRuntime(agent_id=agent_id)
                result = await asyncio.wait_for(
                    runtime.execute(instructions),
                    timeout=self.timeout_seconds,
                )
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"[{agent_id}] Execution finished: {status}")
        except asyncio.TimeoutError:
            logger.error(f"[{agent_id}] Execution timed out after {self.timeout_seconds}s")
            result = ExecutionResult(
                agent_name=_display_name(agent_id),
                agent_id=agent_id,
                success=False,
                response=f"Execution timed out after {self.timeout_seconds} seconds",
                error="Timeout",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(f"[{agent_id}] Execution failed unexpectedly")
            result = ExecutionResult(
                agent_name=_display_name(agent_id),
                agent_id=agent_id,
                success=False,
                response=f"Execution failed: {exc}",
                error=str(exc),
            )
        finally:
            self._pending.pop(request_id, None)

        await self._complete_execution(batch_id, result, agent_id)
        return result

    # Add execution request to current batch or create new batch if none exists
    async def _register_pending_execution(
        self,
        agent_id: str,
        instructions: str,
        request_id: str,
    ) -> str:
        """Attach a new execution to the active batch, opening one when required."""

        async with self._batch_lock:
            if self._batch_state is None:
                batch_id = str(uuid.uuid4())
                self._batch_state = _BatchState(batch_id=batch_id)
            else:
                batch_id = self._batch_state.batch_id

            self._batch_state.pending += 1
            self._pending[request_id] = PendingExecution(
                request_id=request_id,
                agent_id=agent_id,
                instructions=instructions,
                batch_id=batch_id,
            )

            return batch_id

    # Store execution result and send combined batch to interaction agent when complete
    async def _complete_execution(
        self,
        batch_id: str,
        result: ExecutionResult,
        agent_id: str,
    ) -> None:
        """Record the execution result and dispatch when the batch drains."""

        dispatch_payload: Optional[str] = None

        async with self._batch_lock:
            state = self._batch_state
            if state is None or state.batch_id != batch_id:
                logger.warning(f"[{agent_id}] Dropping result for unknown batch")
                return

            state.results.append(result)
            state.pending -= 1

            if state.pending == 0:
                dispatch_payload = self._format_batch_payload(state.results)
                agent_ids = [entry.agent_id for entry in state.results]
                logger.info(f"Execution batch completed: {', '.join(agent_ids)}")
                self._batch_state = None

        if dispatch_payload:
            await self._dispatch_to_interaction_agent(dispatch_payload)

    # Return list of currently pending execution requests for monitoring purposes
    def get_pending_executions(self) -> List[Dict[str, str]]:
        """Expose pending executions for observability."""

        return [
            {
                "request_id": pending.request_id,
                "agent_id": pending.agent_id,
                "batch_id": pending.batch_id,
                "created_at": pending.created_at.isoformat(),
                "elapsed_seconds": (datetime.now() - pending.created_at).total_seconds(),
            }
            for pending in self._pending.values()
        ]

    # Clean up all pending executions and batch state on shutdown
    async def shutdown(self) -> None:
        """Clear pending bookkeeping (no background work remains)."""

        self._pending.clear()
        async with self._batch_lock:
            self._batch_state = None

    # Format multiple execution results into single message for interaction agent
    def _format_batch_payload(self, results: List[ExecutionResult]) -> str:
        """Render execution results into the interaction-agent format."""

        entries: List[str] = []
        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            response_text = (result.response or "(no response provided)").strip()
            # The id rides along in the conversation history, so follow-ups can route straight back
            entries.append(f"[{status}] {result.agent_name} ({result.agent_id}): {response_text}")
        return "\n".join(entries)

    # Forward combined execution results to interaction agent for user response generation
    async def _dispatch_to_interaction_agent(self, payload: str) -> None:
        """Send the aggregated execution summary to the interaction agent."""

        from ..interaction_agent.runtime import InteractionAgentRuntime

        runtime = InteractionAgentRuntime()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(runtime.handle_agent_message(payload))
            return

        loop.create_task(runtime.handle_agent_message(payload))


_shared_manager: Optional[ExecutionBatchManager] = None


# The one batch manager every caller shares, so per-agent locks and batching cover user requests and triggers alike
def get_execution_batch_manager() -> ExecutionBatchManager:
    global _shared_manager
    if _shared_manager is None:
        _shared_manager = ExecutionBatchManager()
    return _shared_manager
