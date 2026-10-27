"""Gmail tool schemas and actions for the execution agent."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable, Dict, List, Optional

from server.services.execution import get_draft_store
from server.services.gmail import execute_gmail_tool, get_active_gmail_user_id

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "gmail_create_draft",
            "description": (
                "Create a Gmail draft (nothing is sent), supporting html/plain bodies, cc/bcc, and attachments. "
                "To reply to an email, create a draft with its thread_id. Report the draft ID so the user can review it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_email": {
                        "type": "string",
                        "description": "Primary recipient email for the draft.",
                    },
                    "subject": {"type": "string", "description": "Email subject."},
                    "body": {
                        "type": "string",
                        "description": "Email body. Use HTML markup when is_html is true.",
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of CC recipient emails.",
                    },
                    "bcc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of BCC recipient emails.",
                    },
                    "extra_recipients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional recipients if the draft should include more addresses.",
                    },
                    "is_html": {
                        "type": "boolean",
                        "description": "Set true when the body contains HTML content.",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Gmail thread id to reply in; set it to make this draft a reply.",
                    },
                    "attachment": {
                        "type": "object",
                        "description": "Single attachment metadata (requires Composio-uploaded asset).",
                        "properties": {
                            "s3key": {"type": "string", "description": "S3 key of uploaded file."},
                            "name": {"type": "string", "description": "Attachment filename."},
                            "mimetype": {"type": "string", "description": "Attachment MIME type."},
                        },
                        "required": ["s3key", "name", "mimetype"],
                    },
                },
                "required": ["recipient_email", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_execute_draft",
            "description": (
                "Send a draft or prepared forward. Only works once the user has seen and approved it; "
                "otherwise it is refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "ID of the draft (e.g. 'r-12') or prepared forward (e.g. 'fwd-3') to send.",
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
            "name": "gmail_forward_email",
            "description": (
                "Prepare forwarding an existing email (nothing is sent). Returns a forward ID (e.g. 'fwd-3'); "
                "report it so the user can review it, then send it with gmail_execute_draft once approved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "Gmail message id to forward.",
                    },
                    "recipient_email": {
                        "type": "string",
                        "description": "Email address to receive the forwarded message.",
                    },
                    "additional_text": {
                        "type": "string",
                        "description": "Optional text to prepend when forwarding.",
                    },
                },
                "required": ["message_id", "recipient_email"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_delete_draft",
            "description": "Delete a draft or discard a prepared forward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {
                        "type": "string",
                        "description": "Identifier of the Gmail draft to delete.",
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
            "name": "gmail_get_contacts",
            "description": "Retrieve Google contacts (connections) available to the authenticated Gmail account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_name": {
                        "type": "string",
                        "description": "Resource name to read contacts from, defaults to people/me.",
                    },
                    "person_fields": {
                        "type": "string",
                        "description": "Comma-separated People API fields to include (e.g. emailAddresses,names).",
                    },
                    "include_other_contacts": {
                        "type": "boolean",
                        "description": "Include other contacts (directory suggestions) when true.",
                    },
                    "page_token": {
                        "type": "string",
                        "description": "Pagination token for retrieving the next page of contacts.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_get_people",
            "description": "Retrieve detailed Google People records or other contacts via Composio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_name": {
                        "type": "string",
                        "description": "Resource name to fetch (defaults to people/me).",
                    },
                    "person_fields": {
                        "type": "string",
                        "description": "Comma-separated People API fields to include in the response.",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Maximum number of people records to return per page.",
                    },
                    "page_token": {
                        "type": "string",
                        "description": "Token to continue fetching the next set of results.",
                    },
                    "sync_token": {
                        "type": "string",
                        "description": "Sync token for incremental sync requests.",
                    },
                    "other_contacts": {
                        "type": "boolean",
                        "description": "Set true to list other contacts instead of connections.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_list_drafts",
            "description": "List Gmail drafts for the connected account using Composio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of drafts to return.",
                    },
                    "page_token": {
                        "type": "string",
                        "description": "Pagination token from a previous drafts list call.",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Include full draft details such as subject and body when true.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail_search_people",
            "description": "Search Google contacts and other people records associated with the Gmail account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to match against names, emails, phone numbers, etc.",
                    },
                    "person_fields": {
                        "type": "string",
                        "description": "Comma-separated fields from the People API to include in results.",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Maximum number of people records to return.",
                    },
                    "other_contacts": {
                        "type": "boolean",
                        "description": "Include other contacts results when true.",
                    },
                    "page_token": {
                        "type": "string",
                        "description": "Pagination token to continue a previous search.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

# Return Gmail tool schemas
def get_schemas() -> List[Dict[str, Any]]:
    """Return Gmail tool schemas."""
    
    return _SCHEMAS


# Execute a Gmail tool; the calling agent's runtime records it in that agent's own journal
def _execute(tool_name: str, composio_user_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a Gmail tool with the None-valued arguments dropped."""

    payload = {k: v for k, v in arguments.items() if v is not None}
    return execute_gmail_tool(tool_name, composio_user_id, arguments=payload)


# Create a Gmail draft via Composio with support for HTML, attachments, and threading
def gmail_create_draft(
    recipient_email: str,
    subject: str,
    body: str,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    extra_recipients: Optional[List[str]] = None,
    is_html: Optional[bool] = None,
    thread_id: Optional[str] = None,
    attachment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "cc": cc,
        "bcc": bcc,
        "extra_recipients": extra_recipients,
        "is_html": is_html,
        "thread_id": thread_id,
        "attachment": attachment,
    }
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_CREATE_EMAIL_DRAFT", composio_user_id, arguments)


# Send an approved draft or prepared forward; anything the user hasn't seen and approved is refused (P20)
def gmail_execute_draft(
    draft_id: str,
) -> Dict[str, Any]:
    store = get_draft_store()
    draft = store.get(draft_id)
    if draft is None or draft.status != "approved":
        state = f"is {draft.status}" if draft else "is not on record"
        return {
            "error": (
                f"Not sent: draft {draft_id!r} {state}. Only a draft the user has seen and approved can be sent. "
                "Report the draft ID so Poke can show it to the user and get their approval."
            )
        }
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    if draft.kind == "forward":
        arguments = {"message_id": draft.message_id, "recipient_email": draft.recipient,
                     "additional_text": draft.body or None}
        result = _execute("GMAIL_FORWARD_MESSAGE", composio_user_id, arguments)
    else:
        result = _execute("GMAIL_SEND_DRAFT", composio_user_id, {"draft_id": draft_id})
    if isinstance(result, dict) and not result.get("error") and result.get("successful") is not False:
        store.mark_sent(draft_id)
    return result


# Prepare a forward of an existing email; it is only sent once the user approves it
def gmail_forward_email(
    message_id: str,
    recipient_email: str,
    additional_text: Optional[str] = None,
    *,
    agent_id: str = "",
) -> Dict[str, Any]:
    forward = get_draft_store().record_forward(
        agent_id=agent_id, message_id=message_id, recipient=recipient_email, note=additional_text or "",
    )
    return {
        "successful": True,
        "data": {"id": forward.id},
        "status": "awaiting_approval",
        "message": f"Not sent yet. Report forward {forward.id} so the user can review it; "
                   "send it with gmail_execute_draft once they approve.",
    }


# Delete a Gmail draft, or discard a prepared forward (which exists only in the draft store)
def gmail_delete_draft(
    draft_id: str,
) -> Dict[str, Any]:
    draft = get_draft_store().get(draft_id)
    if draft is not None and draft.kind == "forward":
        return {"successful": True, "data": {}}  # the runtime marks it deleted, as for any draft
    arguments = {"draft_id": draft_id}
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_DELETE_DRAFT", composio_user_id, arguments)


def gmail_get_contacts(
    resource_name: Optional[str] = None,
    person_fields: Optional[str] = None,
    include_other_contacts: Optional[bool] = None,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    arguments = {
        "resource_name": resource_name,
        "person_fields": person_fields,
        "include_other_contacts": include_other_contacts,
        "page_token": page_token,
    }
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_GET_CONTACTS", composio_user_id, arguments)


def gmail_get_people(
    resource_name: Optional[str] = None,
    person_fields: Optional[str] = None,
    page_size: Optional[int] = None,
    page_token: Optional[str] = None,
    sync_token: Optional[str] = None,
    other_contacts: Optional[bool] = None,
) -> Dict[str, Any]:
    arguments = {
        "resource_name": resource_name,
        "person_fields": person_fields,
        "page_size": page_size,
        "page_token": page_token,
        "sync_token": sync_token,
        "other_contacts": other_contacts,
    }
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_GET_PEOPLE", composio_user_id, arguments)


def gmail_list_drafts(
    max_results: Optional[int] = None,
    page_token: Optional[str] = None,
    verbose: Optional[bool] = None,
) -> Dict[str, Any]:
    arguments = {
        "max_results": max_results,
        "page_token": page_token,
        "verbose": verbose,
    }
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_LIST_DRAFTS", composio_user_id, arguments)


def gmail_search_people(
    query: str,
    person_fields: Optional[str] = None,
    page_size: Optional[int] = None,
    other_contacts: Optional[bool] = None,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {
        "query": query,
        "person_fields": person_fields,
        "other_contacts": other_contacts,
    }
    if page_size is not None:
        arguments["pageSize"] = page_size
    if page_token is not None:
        arguments["pageToken"] = page_token
    composio_user_id = get_active_gmail_user_id()
    if not composio_user_id:
        return {"error": "Gmail not connected. Please connect Gmail in settings first."}
    return _execute("GMAIL_SEARCH_PEOPLE", composio_user_id, arguments)


# Return Gmail tool callables
def build_registry(agent_id: str) -> Dict[str, Callable[..., Any]]:
    """Return Gmail tool callables."""
    
    return {
        "gmail_create_draft": gmail_create_draft,
        "gmail_execute_draft": gmail_execute_draft,
        "gmail_delete_draft": gmail_delete_draft,
        "gmail_forward_email": partial(gmail_forward_email, agent_id=agent_id),
        "gmail_get_contacts": gmail_get_contacts,
        "gmail_get_people": gmail_get_people,
        "gmail_list_drafts": gmail_list_drafts,
        "gmail_search_people": gmail_search_people,
    }


__all__ = [
    "build_registry",
    "get_schemas",
    "gmail_create_draft",
    "gmail_execute_draft",
    "gmail_delete_draft",
    "gmail_forward_email",
    "gmail_get_contacts",
    "gmail_get_people",
    "gmail_list_drafts",
    "gmail_search_people",
]
