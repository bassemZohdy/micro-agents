"""Map between ADK events and the Micro-Agent session transcript layout.

Every runtime stores a session as ``{"role", "content", ...}`` messages (user,
assistant with optional ``tool_calls``, and ``tool`` results) under the plain
session id. ADK keeps richer events (state deltas, confirmation requests), so the
ADK session service stores *both*: the canonical messages, which any runtime can
read, and the native events, which ADK reloads losslessly while the messages are
unchanged. When another runtime appended to the session, the events are rebuilt
from the messages. This module is that mapping and nothing else.
"""

# Google ADK is optional and does not publish a py.typed marker.
# mypy: disable_error_code="import-not-found,import-untyped,misc"

from __future__ import annotations

import hashlib
import json
from typing import Any

#: ADK's synthetic call used to ask a human to confirm a tool. It is protocol
#: plumbing, not conversation, so it never reaches the portable transcript.
CONFIRMATION_FUNCTION = "adk_request_confirmation"


def content_to_messages(content: Any) -> list[dict[str, Any]]:
    """Translate one ADK ``Content`` into zero or more transcript messages."""
    role = "assistant" if getattr(content, "role", "user") == "model" else "user"
    parts = [p for p in (getattr(content, "parts", []) or []) if not getattr(p, "thought", False)]
    text = "".join(str(part.text) for part in parts if getattr(part, "text", None))
    tool_calls = [
        {
            "id": getattr(part.function_call, "id", None),
            "type": "function",
            "function": {
                "name": getattr(part.function_call, "name", ""),
                "arguments": json.dumps(getattr(part.function_call, "args", {}) or {}),
            },
        }
        for part in parts
        if getattr(part, "function_call", None)
        and getattr(part.function_call, "name", "") != CONFIRMATION_FUNCTION
    ]
    tool_responses = [
        {
            "role": "tool",
            "tool_call_id": getattr(part.function_response, "id", None),
            "name": getattr(part.function_response, "name", ""),
            "content": json.dumps(
                getattr(part.function_response, "response", {}) or {}, default=str
            ),
        }
        for part in parts
        if getattr(part, "function_response", None)
        and getattr(part.function_response, "name", "") != CONFIRMATION_FUNCTION
    ]
    if tool_responses:
        return tool_responses
    if tool_calls:
        return [{"role": "assistant", "content": text, "tool_calls": tool_calls}]
    if not text:
        return []
    return [{"role": role, "content": text}]


def events_to_messages(events: list[Any]) -> list[dict[str, Any]]:
    """Portable transcript for a list of ADK events (partial events skipped)."""
    messages: list[dict[str, Any]] = []
    for event in events:
        if getattr(event, "partial", False) or getattr(event, "content", None) is None:
            continue
        messages.extend(content_to_messages(event.content))
    return messages


def messages_to_events(messages: list[dict[str, Any]], author: str) -> list[Any]:
    """Rebuild ADK events from transcript messages written by any runtime."""
    from google.adk.events.event import Event
    from google.genai import types

    events: list[Any] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            content = types.Content(
                role="user", parts=[types.Part.from_text(text=str(message.get("content", "")))]
            )
            events.append(Event(author="user", content=content))
        elif role == "assistant":
            parts: list[Any] = []
            if message.get("content"):
                parts.append(types.Part.from_text(text=str(message["content"])))
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            id=call.get("id"),
                            name=function.get("name", ""),
                            args=_loads(function.get("arguments")),
                        )
                    )
                )
            if parts:
                events.append(
                    Event(author=author, content=types.Content(role="model", parts=parts))
                )
        elif role == "tool":
            response = _loads_any(message.get("content"))
            if not isinstance(response, dict):
                response = {"result": response}
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message.get("tool_call_id"),
                    name=str(message.get("name", "")),
                    response=response,
                )
            )
            events.append(Event(author=author, content=types.Content(role="user", parts=[part])))
    return events


def transcript_digest(messages: list[dict[str, Any]]) -> str:
    """Stable digest of a transcript, used to detect writes by another runtime."""
    encoded = json.dumps(messages, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _loads(raw: Any) -> dict[str, Any]:
    value = _loads_any(raw)
    return value if isinstance(value, dict) else {}


def _loads_any(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw
