from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..models import ChatHistoryClearResponse, ChatHistoryResponse, ChatRequest
from ..services import get_conversation_log, get_trigger_service, handle_chat_request
from ..services.conversation.stream import conversation_event_stream

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/send", response_class=JSONResponse, summary="Submit a chat message and receive a completion")
# Handle incoming chat messages and route them to the interaction agent
async def chat_send(
    payload: ChatRequest,
) -> JSONResponse:
    return await handle_chat_request(payload)


@router.get("/history", response_model=ChatHistoryResponse)
# Retrieve the conversation history from the log
def chat_history() -> ChatHistoryResponse:
    log = get_conversation_log()
    return ChatHistoryResponse(messages=log.to_chat_messages())


@router.get("/events", summary="Live conversation events (Server-Sent Events)")
# Push new messages and the assistant's working state to the browser instead of having it re-poll the history (P18)
async def chat_events(
    request: Request,
    after: Optional[int] = Query(default=None, ge=0, description="Send messages after this sequence number"),
    last_event_id: Optional[str] = Header(default=None),
) -> StreamingResponse:
    # A reconnecting EventSource sends the id of the last event it got; an explicit ?after= wins
    start = after if after is not None else (int(last_event_id) if (last_event_id or "").isdigit() else 0)
    return StreamingResponse(
        conversation_event_stream(start, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/history", response_model=ChatHistoryClearResponse)
def clear_history() -> ChatHistoryClearResponse:
    from ..services import get_agent_registry, get_draft_store, get_execution_agent_logs
    from ..services.notifications import get_notification_center

    # Clear conversation log
    log = get_conversation_log()
    log.clear()

    # Clear execution agent logs
    execution_logs = get_execution_agent_logs()
    execution_logs.clear_all()

    # Clear the agent registry and its drafts
    get_agent_registry().clear()
    get_draft_store().clear()

    # Clear pending and past notifications
    get_notification_center().clear()

    # Clear stored triggers
    trigger_service = get_trigger_service()
    trigger_service.clear_all()

    return ChatHistoryClearResponse()


__all__ = ["router"]
