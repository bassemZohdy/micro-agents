"""A2A server transport: JSON-RPC binding bridged onto Micro-Agent invocations.

The official a2a-sdk v1 server stack serves the agent card and JSON-RPC
``SendMessage``/``SendStreamingMessage`` methods; :class:`MicroAgentExecutor` maps
a received message onto a Micro-Agent invocation and drives the standard task
lifecycle (submitted → working → completed, or failed). Authentication stays
at the HTTP transport layer — the same middleware that guards the native API
also guards the RPC route when caller identity is required.
"""

# mypy: disable_error_code="attr-defined,name-defined,misc,untyped-decorator"

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.interoperability.a2a import (
    A2aSdkUnavailableError,
    agent_card_from_definition,
)
from micro_agent.interoperability.a2a_store import HttpxPushNotificationSender


def _import_sdk() -> Any:  # noqa in sync with mypy: dynamic namespace below
    try:
        from a2a.server.agent_execution import AgentExecutor, RequestContext
        from a2a.server.events import EventQueue
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.routes.agent_card_routes import create_agent_card_routes
        from a2a.server.routes.common import DefaultServerCallContextBuilder
        from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
        from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
        from a2a.types import Part, Task, TaskState, TaskStatus

        class _Sdk:
            pass

        sdk = _Sdk()
        # Dynamic namespace; mypy attr-defined is silenced at module level.
        sdk.DefaultRequestHandler = DefaultRequestHandler
        sdk.create_agent_card_routes = create_agent_card_routes
        sdk.create_jsonrpc_routes = create_jsonrpc_routes
        sdk.DefaultServerCallContextBuilder = DefaultServerCallContextBuilder
        sdk.InMemoryTaskStore = InMemoryTaskStore
        sdk.TaskUpdater = TaskUpdater
        sdk.Part = Part
        sdk.Task = Task
        sdk.TaskState = TaskState
        sdk.TaskStatus = TaskStatus

        sdk.AgentExecutor = AgentExecutor
        sdk.RequestContext = RequestContext
        sdk.EventQueue = EventQueue

        class _MicroAgentCallContextBuilder(DefaultServerCallContextBuilder):
            """Copy verified HTTP identity into the SDK task-store context."""

            def build(self, request: Any) -> Any:
                context = super().build(request)
                identity = getattr(request.state, "identity", None)
                user = getattr(identity, "user", None)
                tenant_id = getattr(user, "tenant_id", None)
                if tenant_id:
                    context.state["tenant_id"] = str(tenant_id)
                return context

        sdk.MicroAgentCallContextBuilder = _MicroAgentCallContextBuilder
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


def _streaming_enabled(agent: Any) -> bool:
    """Return whether the bound runtime can produce stream events."""
    capabilities = getattr(agent, "runtime_capabilities", None)
    if callable(capabilities):
        capabilities = capabilities()
    return bool(getattr(capabilities, "streaming", False))


def _response_text(response: Any) -> str:
    """Map a terminal Micro-Agent response to an A2A text artifact."""
    output = getattr(response, "output", {})
    content = output.get("content") if isinstance(output, dict) else None
    return str(content) if content else json.dumps(output, default=str)


def _text_part(sdk: Any, text: str) -> Any:
    """Build a v1 A2A text part."""
    try:
        return sdk.Part(text=text)
    except TypeError:  # pragma: no cover - compatibility with the old SDK seam
        return sdk.Part(root=sdk.TextPart(text=text))


async def _enqueue_initial_task(sdk: Any, event_queue: Any, task_id: str, context_id: str) -> bool:
    """Seed the v1 event stream before status updates are emitted."""
    enqueue = getattr(event_queue, "enqueue_event", None)
    if not callable(enqueue):
        return False
    await enqueue(
        sdk.Task(
            id=task_id,
            context_id=context_id,
            status=sdk.TaskStatus(state=sdk.TaskState.TASK_STATE_SUBMITTED),
        )
    )
    return True


def build_micro_agent_executor(agent: DefaultMicroAgent) -> Any:
    """Build the A2A AgentExecutor bridge for a Micro-Agent."""
    sdk = _import_sdk()

    class MicroAgentExecutor(sdk.AgentExecutor):
        def __init__(self, agent: DefaultMicroAgent) -> None:
            self._agent = agent
            self._in_flight: dict[str, asyncio.Task[Any]] = {}

        async def _stream_response(self, request: AgentRequest, updater: Any) -> Any:
            """Publish provider deltas as one appendable A2A artifact."""
            artifact_id = f"{updater.task_id}:result"
            emitted_text = ""
            pending_delta = ""
            emitted_artifact = False
            final_response = None

            async for event in self._agent.stream(request):
                delta = getattr(event, "delta", "")
                if delta:
                    if pending_delta:
                        await updater.add_artifact(
                            [_text_part(sdk, pending_delta)],
                            artifact_id=artifact_id,
                            name="result",
                            append=emitted_artifact,
                            last_chunk=False,
                        )
                        emitted_text += pending_delta
                        emitted_artifact = True
                    pending_delta = delta
                response = getattr(event, "response", None)
                if response is not None:
                    final_response = response

            if final_response is None:
                raise RuntimeError("A2A stream ended without a final response")

            final_text = _response_text(final_response)
            combined_text = emitted_text + pending_delta
            if final_text.startswith(combined_text):
                final_delta = pending_delta + final_text[len(combined_text) :]
            else:
                # Keep the artifact complete even if a custom provider does
                # not repeat its accumulated content in the final event.
                final_delta = final_text if not emitted_artifact else pending_delta
            if final_delta or not emitted_artifact:
                await updater.add_artifact(
                    [_text_part(sdk, final_delta)],
                    artifact_id=artifact_id,
                    name="result",
                    append=emitted_artifact,
                    last_chunk=True,
                )
            return final_response

        async def execute(self, context: Any, event_queue: Any) -> None:
            task_id = context.task_id or str(uuid4())
            context_id = context.context_id or task_id
            updater = sdk.TaskUpdater(event_queue, task_id=task_id, context_id=context_id)
            current_task = asyncio.current_task()
            if current_task is not None:
                self._in_flight[task_id] = current_task
            try:
                seeded_task = await _enqueue_initial_task(sdk, event_queue, task_id, context_id)
                if not seeded_task:
                    await updater.submit()
                await updater.start_work()
                request = AgentRequest(input=_payload_from(context), session_id=context_id)
                if _streaming_enabled(self._agent):
                    response = await self._stream_response(request, updater)
                else:
                    response = await self._agent.invoke(request)
            except asyncio.CancelledError:
                await updater.cancel()
                raise
            except Exception:  # noqa: BLE001 — failures become task failures
                await updater.failed(
                    updater.new_agent_message([_text_part(sdk, "agent invocation failed")])
                )
                return
            else:
                if not _streaming_enabled(self._agent):
                    await updater.add_artifact(
                        [_text_part(sdk, _response_text(response))],
                        name="result",
                    )
                await updater.complete()
            finally:
                if current_task is not None and self._in_flight.get(task_id) is current_task:
                    del self._in_flight[task_id]

        async def cancel(self, context: Any, event_queue: Any) -> None:
            task_id = context.task_id or str(uuid4())
            task = self._in_flight.get(task_id)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                return
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
    task_store: Any | None = None,
    push_config_store: Any | None = None,
    push_sender: Any | None = None,
) -> dict[str, str]:
    """Mount the standard A2A routes onto the FastAPI app.

    The agent card is served at ``/.well-known/agent-card.json``; when
    ``enable_rpc`` is true (the definition enables A2A), the JSON-RPC
    transport is mounted at ``/`` with streaming enabled only when the bound
    runtime advertises it.
    Returns the mounted paths.
    """
    sdk = _import_sdk()
    card = agent_card_from_definition(
        agent.definition,
        base_url=base_url,
        streaming=_streaming_enabled(agent),
        push_notifications=push_config_store is not None,
    )
    paths = {
        "card": "/.well-known/agent-card.json",
        "protocol_version": card.supported_interfaces[0].protocol_version,
    }

    if enable_rpc:
        # A caller that supplies a durable store owns its lifecycle.  The
        # default remains the SDK's process-local store for compatibility.
        if task_store is None:
            from a2a.server.tasks import InMemoryTaskStore

            task_store = InMemoryTaskStore()
        if push_config_store is not None and push_sender is None:
            push_sender = HttpxPushNotificationSender(push_config_store)
        handler = sdk.DefaultRequestHandler(
            agent_executor=build_micro_agent_executor(agent),
            task_store=task_store,
            agent_card=card,
            push_config_store=push_config_store,
            push_sender=push_sender,
        )
        context_builder = sdk.MicroAgentCallContextBuilder()
        for route in sdk.create_agent_card_routes(card, card_url=paths["card"]):
            app.router.routes.append(route)
        for route in sdk.create_jsonrpc_routes(
            handler, rpc_url="/", context_builder=context_builder
        ):
            app.router.routes.append(route)
        paths["rpc"] = "/"
    else:
        from a2a.server.request_handlers.response_helpers import agent_card_to_dict
        from fastapi.responses import JSONResponse

        @app.get(paths["card"], response_model=None)
        async def get_agent_card() -> JSONResponse:
            return JSONResponse(agent_card_to_dict(card))

    return paths


__all__ = ["attach_a2a", "build_micro_agent_executor"]
