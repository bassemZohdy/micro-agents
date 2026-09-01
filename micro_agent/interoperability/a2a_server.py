"""A2A server transport: JSON-RPC binding bridged onto Micro-Agent invocations.

The official a2a-sdk server stack serves the agent card and the JSON-RPC
``message/send`` method; :class:`MicroAgentExecutor` maps a received message
onto a Micro-Agent invocation and drives the standard task lifecycle
(submitted → working → completed, or failed). Authentication stays at the
HTTP transport layer — the same middleware that guards the native API also
guards the RPC route when caller identity is required.
"""

# mypy: disable_error_code="attr-defined,name-defined,misc,untyped-decorator"

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.interoperability.a2a import (
    A2aSdkUnavailableError,
    agent_card_from_definition,
)


def _import_sdk() -> Any:  # noqa in sync with mypy: dynamic namespace below
    try:
        from a2a.server.apps.jsonrpc import A2AFastAPIApplication
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
        from a2a.types import Part, TextPart

        class _Sdk:
            pass

        sdk = _Sdk()
        # Dynamic namespace; mypy attr-defined is silenced at module level.
        sdk.A2AFastAPIApplication = A2AFastAPIApplication
        sdk.DefaultRequestHandler = DefaultRequestHandler
        sdk.InMemoryTaskStore = InMemoryTaskStore
        sdk.TaskUpdater = TaskUpdater
        sdk.Part = Part
        sdk.TextPart = TextPart
        from a2a.server.agent_execution import AgentExecutor, RequestContext
        from a2a.server.events import EventQueue

        sdk.AgentExecutor = AgentExecutor
        sdk.RequestContext = RequestContext
        sdk.EventQueue = EventQueue
        return sdk
    except ImportError as exc:
        raise A2aSdkUnavailableError() from exc


def _payload_from(context: Any) -> dict[str, Any]:
    """Map the inbound A2A message text onto an invocation input payload."""
    text = context.get_user_input()
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"message": text}
    return parsed if isinstance(parsed, dict) else {"message": text}


def build_micro_agent_executor(agent: DefaultMicroAgent) -> Any:
    """Build the A2A AgentExecutor bridge for a Micro-Agent."""
    sdk = _import_sdk()

    class MicroAgentExecutor(sdk.AgentExecutor):
        def __init__(self, agent: DefaultMicroAgent) -> None:
            self._agent = agent

        async def execute(self, context: Any, event_queue: Any) -> None:
            task_id = context.task_id or str(uuid4())
            context_id = context.context_id or task_id
            updater = sdk.TaskUpdater(event_queue, task_id=task_id, context_id=context_id)
            await updater.submit()
            await updater.start_work()
            try:
                response = await self._agent.invoke(
                    AgentRequest(input=_payload_from(context), session_id=context_id)
                )
            except Exception:  # noqa: BLE001 — failures become task failures
                await updater.failed(
                    updater.new_agent_message(
                        [sdk.Part(root=sdk.TextPart(text="agent invocation failed"))]
                    )
                )
                return
            output = response.output
            content = output.get("content") if isinstance(output, dict) else None
            text = str(content) if content else json.dumps(output, default=str)
            await updater.add_artifact(
                [sdk.Part(root=sdk.TextPart(text=text))],
                name="result",
            )
            await updater.complete()

        async def cancel(self, context: Any, event_queue: Any) -> None:
            # Invocations complete within a single request; a cancel marks
            # the task canceled in the task store.
            task_id = context.task_id or str(uuid4())
            context_id = context.context_id or task_id
            updater = sdk.TaskUpdater(event_queue, task_id=task_id, context_id=context_id)
            await updater.cancel()

    return MicroAgentExecutor(agent)


def attach_a2a(
    app: Any,
    agent: DefaultMicroAgent,
    *,
    base_url: str | None = None,
    security_scheme: dict[str, Any] | None = None,
    enable_rpc: bool = False,
) -> dict[str, str]:
    """Mount the standard A2A routes onto the FastAPI app.

    The agent card is served at ``/.well-known/agent-card.json``; when
    ``enable_rpc`` is true (the definition enables A2A), the JSON-RPC
    transport is mounted at ``/`` with a full non-streaming task lifecycle.
    Returns the mounted paths.
    """
    sdk = _import_sdk()
    card = agent_card_from_definition(agent.definition, base_url=base_url)
    paths = {
        "card": "/.well-known/agent-card.json",
        "protocol_version": card.protocol_version,
    }

    if enable_rpc:
        handler = sdk.DefaultRequestHandler(
            agent_executor=build_micro_agent_executor(agent),
            task_store=sdk.InMemoryTaskStore(),
        )
        a2a_app = sdk.A2AFastAPIApplication(agent_card=card, http_handler=handler)
        a2a_app.add_routes_to_app(app, agent_card_url=paths["card"], rpc_url="/")
        paths["rpc"] = "/"
    else:
        from fastapi.responses import JSONResponse

        @app.get(paths["card"], response_model=None)
        async def get_agent_card() -> JSONResponse:
            return JSONResponse(card.model_dump(by_alias=True, exclude_none=True, mode="json"))

    return paths


__all__ = ["attach_a2a", "build_micro_agent_executor"]
