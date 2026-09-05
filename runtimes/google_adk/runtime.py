"""Runtime-neutral adapter for Google's Agent Development Kit (ADK).

Google ADK is imported lazily so the core package remains usable without the
optional ``google-adk`` extra.  Only :class:`RuntimeAgent` and the Micro-Agent
request/response contracts cross this module's public boundary; ADK objects
are retained in the opaque runtime-agent handle.

Declared services map onto ADK-native constructs:

- sessions map to ADK session services,
- a declared memory dependency maps to an ADK ``BaseMemoryService`` bridge
  over the Micro-Agent ``MemoryProvider``,
- policy maps to a deterministic evaluator wrapped around every ADK tool
  execution and to a startup check of declared MCP servers,
- declared MCP servers map to ADK tools discovered through the injected
  :class:`McpConnectionManager`,
- telemetry maps to spans, metrics, and structured logs around the runner,
- checkpointing maps the runtime-neutral store onto ADK's resumable invocation
  flow and stores an event snapshot for cross-process restoration.

External session providers map onto an ADK session-service bridge, and the
runtime-neutral operation registry wraps side-effect tools for distributed
idempotency. Knowledge providers use the same runtime-neutral retrieval
contract as the custom runtime and are injected as bounded reference context
per invocation.
"""

# Google ADK is intentionally optional.  The dedicated ADK CI job installs its
# type-bearing package; the default typecheck job must still validate this
# module when those imports are unavailable.
# Google ADK is an optional dependency and currently ships without type
# metadata. Keep strict checking for the adapter itself while treating the
# dynamically imported SDK surface as an untyped boundary.
# mypy: disable_error_code="import-not-found,import-untyped,misc"

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from micro_agent.checkpoint import CheckpointRecord, CheckpointStore
from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    AgentStreamEvent,
    CheckpointNotFoundError,
    ContinuationNotFoundError,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.health import DependencyProbe, HealthStatus
from micro_agent.knowledge import (
    KnowledgeRetriever,
    KnowledgeSource,
    build_knowledge_query,
    retrieve_knowledge_context,
)
from micro_agent.mcp import McpConnectionManager
from micro_agent.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.models import (
    ModelConfig,
    ModelProvider,
    ProviderCapabilities,
    structured_output_generation,
)
from micro_agent.observability import AuditSink, Telemetry
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.security import (
    AgentPolicy,
    InvocationIdentity,
    Operation,
    OperationRegistryProtocol,
    OperationResult,
    PolicyEvaluator,
    RetryClassification,
    get_invocation_identity,
    reset_invocation_identity,
    resolve_workload_identity,
    set_invocation_identity,
)
from micro_agent.session import SessionProvider
from micro_agent.tools import Tool, builtin_tool_registry, normalize_tool_side_effect
from micro_agent.tools.plugin import load_plugin_tools

_ADK_REQUEST_CONFIRMATION_NAME = "adk_request_confirmation"


async def _maybe_await(value: Any) -> Any:
    """Normalize sync local providers and async external providers."""
    if inspect.isawaitable(value):
        return await value
    return value


class GoogleAdkError(RuntimeError):
    """Raised when Google ADK cannot be loaded or configured."""


@dataclass
class GoogleAdkRuntimeConfig:
    """Configuration for the Google ADK adapter.

    ``model_factory`` and the service factories are dependency-injection seams
    for tests and deployments that already own ADK services.  When omitted,
    the adapter uses ADK in-memory session/memory services and a model string
    from the definition.  A Micro-Agent ``ModelProvider`` can be supplied to
    bridge existing providers (including the deterministic fake provider)
    through ADK's ``BaseLlm`` interface.  ``mcp_manager``, ``policy``,
    ``telemetry``, and ``memory_provider`` map the corresponding declared
    services onto the adapter; unsupported declarations are rejected by the
    bootstrap before this adapter is constructed.
    """

    model_provider: ModelProvider | None = None
    model_factory: Callable[[MicroAgentDefinition], Any] | None = None
    session_service_factory: Callable[[], Any] | None = None
    memory_service_factory: Callable[[], Any] | None = None
    runner_factory: Callable[..., Any] | None = None
    tool_registry: dict[str, Tool] = field(default_factory=dict)
    knowledge_provider: KnowledgeRetriever | None = None
    mcp_manager: McpConnectionManager | None = None
    policy: AgentPolicy | None = None
    audit: AuditSink | None = None
    telemetry: Telemetry | None = None
    memory_provider: MemoryProvider | None = None
    memory_policy: MemoryPolicy | None = None
    session_provider: SessionProvider | None = None
    operation_registry: OperationRegistryProtocol | None = None
    checkpoint_store: CheckpointStore | None = None
    app_name_prefix: str = "micro-agent"
    user_id: str = "micro-agent-user"
    model_api_key: str | None = field(default=None, repr=False)

    @property
    def effective_memory_policy(self) -> MemoryPolicy:
        return self.memory_policy or MemoryPolicy()


class GoogleAdkRuntime(AgentRuntime):
    """Google ADK implementation behind the runtime-neutral SPI."""

    def __init__(self, config: GoogleAdkRuntimeConfig | None = None) -> None:
        self._config = config or GoogleAdkRuntimeConfig()
        self._runners: list[Any] = []
        self._model_provider = self._config.model_provider
        self._native_model_clients: list[Any] = []
        self._telemetry = self._config.telemetry or Telemetry.from_environment()
        self._policy_evaluator = (
            PolicyEvaluator(self._config.policy) if self._config.policy is not None else None
        )
        self._workload_identity = resolve_workload_identity()
        self._knowledge_refs: list[KnowledgeSource] = []

    def capabilities(self) -> RuntimeCapabilities:
        """Report only capabilities implemented by this adapter path."""
        provider_capabilities = (
            self._model_provider.capabilities() if self._model_provider is not None else None
        )
        return RuntimeCapabilities(
            streaming=bool(provider_capabilities and provider_capabilities.streaming),
            memory=self._config.memory_provider is not None,
            mcp=self._config.mcp_manager is not None,
            a2a=False,
            structured_output=bool(
                provider_capabilities and provider_capabilities.structured_output
            ),
            checkpointing=self._config.checkpoint_store is not None,
        )

    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        """Construct an ADK agent, session service, and runner from a definition."""
        llm_agent_cls, runner_cls, session_service_cls = _load_adk()

        model_ref = definition.spec.dependencies.model
        model_config = ModelConfig(
            ref=model_ref.ref if model_ref else "default",
            provider=model_ref.provider if model_ref else None,
            model_id=model_ref.model_id if model_ref else None,
            endpoint=model_ref.endpoint if model_ref else None,
            credential_ref=model_ref.credential_ref if model_ref else None,
            generation=structured_output_generation(
                model_ref.generation if model_ref else {},
                definition.spec.behavior.output_contract,
                self._model_provider.capabilities()
                if self._model_provider is not None
                else ProviderCapabilities(),
            ),
            timeout_seconds=model_ref.timeout_seconds if model_ref else None,
        )
        adk_model = self._adk_model(definition, model_config)
        tools = self._resolve_tools(definition)
        tool_side_effects = {
            tool_definition.name: tool_definition.side_effect
            for tool_definition in definition.spec.dependencies.tools
        }
        adk_tools = [
            _as_adk_tool(
                tool,
                evaluator=self._policy_evaluator,
                telemetry=self._telemetry,
                audit=self._config.audit,
                operation_registry=self._config.operation_registry,
                side_effect=tool_side_effects.get(tool.metadata.name),
            )
            for tool in tools.values()
        ]
        adk_agent_name = _adk_name(definition.metadata.name)
        adk_agent = llm_agent_cls(
            name=adk_agent_name,
            model=adk_model,
            instruction=definition.spec.behavior.instructions,
            tools=adk_tools,
        )

        app_name = f"{self._config.app_name_prefix}-{definition.metadata.name}"
        session_service = (
            self._config.session_service_factory()
            if self._config.session_service_factory is not None
            else (
                _provider_session_service(self._config.session_provider)
                if self._config.session_provider is not None
                else session_service_cls()
            )
        )
        memory_service = self._build_memory_service()
        runner_kwargs: dict[str, Any] = {
            "agent": adk_agent,
            "app_name": app_name,
            "session_service": session_service,
            "memory_service": memory_service,
        }
        if self._config.runner_factory is not None:
            if self._config.checkpoint_store is not None:
                runner_kwargs["resumability_config"] = _adk_resumability_config()
            runner = self._config.runner_factory(**runner_kwargs)
        else:
            if self._config.checkpoint_store is not None:
                try:
                    from google.adk.apps import App
                except ImportError as exc:  # pragma: no cover - guarded by _load_adk
                    raise GoogleAdkError("Google ADK resumability APIs are unavailable") from exc
                runner = runner_cls(
                    app=App(
                        name=app_name,
                        root_agent=adk_agent,
                        resumability_config=_adk_resumability_config(),
                    ),
                    session_service=session_service,
                    memory_service=memory_service,
                )
            else:
                runner = runner_cls(**runner_kwargs)
        self._runners.append(runner)

        identity = AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        )
        self._telemetry.logger.set_context(
            agent_id=identity.agent_id,
            agent_version=identity.agent_version,
        )
        memory_scope = (
            definition.spec.dependencies.memory.scope
            if definition.spec.dependencies.memory
            else None
        )
        self._knowledge_refs = [
            KnowledgeSource(
                ref=ref.ref,
                source_type=ref.source_type,
                version=ref.version,
                max_results=ref.max_results,
                max_context_characters=ref.max_context_characters,
            )
            for ref in definition.spec.dependencies.knowledge
        ]
        return RuntimeAgent(
            identity=identity,
            capabilities=AgentCapabilities(memory=self._config.memory_provider is not None),
            _internal={
                "definition": definition,
                "adk_agent": adk_agent,
                "adk_runner": runner,
                "adk_session_service": session_service,
                "adk_memory_service": memory_service,
                "memory_scope": memory_scope or "agent",
                "app_name": app_name,
                "user_id": self._config.user_id,
                "model_config": model_config,
                "tool_side_effects": tool_side_effects,
                "mcp_tools_appended": False,
                "started": False,
            },
        )

    async def start(self, agent: RuntimeAgent) -> None:
        """Check injected providers and connect declared MCP servers before ready."""
        if self._model_provider is not None and not await self._model_provider.health_check():
            raise RuntimeError("model provider failed its health check at startup")

        checkpoint_store = self._config.checkpoint_store
        if checkpoint_store is not None:
            try:
                healthy = await checkpoint_store.health_check()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("checkpoint store failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("checkpoint store failed its health check at startup")

        operation_registry = self._config.operation_registry
        if operation_registry is not None:
            try:
                healthy = await _maybe_await(operation_registry.health_check())
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("operation registry failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("operation registry failed its health check at startup")

        memory_provider = self._config.memory_provider
        if memory_provider is not None:
            try:
                await memory_provider.list_entries()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("memory provider failed its health check at startup") from exc

        knowledge_provider = self._config.knowledge_provider
        if knowledge_provider is not None:
            for source in self._knowledge_refs:
                try:
                    source_available = await knowledge_provider.health_check(source)
                except Exception as exc:  # noqa: BLE001 — normalize startup failures
                    raise RuntimeError(
                        "knowledge provider failed its health check at startup"
                    ) from exc
                if not source_available:
                    raise RuntimeError(f"knowledge source '{source.ref}' is not available")

        definition: MicroAgentDefinition = agent._internal["definition"]

        # Capability negotiation: declaring tools against a provider that
        # cannot call them must fail at startup, not silently drop them.
        # The native Google model path handles tools inside ADK.
        declared_tools = definition.spec.dependencies.tools
        if (
            declared_tools
            and self._model_provider is not None
            and not self._model_provider.capabilities().tool_use
        ):
            raise RuntimeError(
                "model provider does not support tool use; "
                f"{len(declared_tools)} declared tools cannot be offered"
            )

        # Deterministic platform policy: denied MCP servers, skills, or models
        # fail startup instead of surfacing as per-call denials later.
        if self._policy_evaluator is not None:
            for server in definition.spec.dependencies.mcp_servers:
                decision = self._policy_evaluator.evaluate_mcp(server.ref)
                if not decision.allowed:
                    self._audit("policy.mcp_denied", mcp=server.ref, reason=decision.reason)
                    raise PermissionError(decision.reason)
            for skill in definition.spec.dependencies.skills:
                decision = self._policy_evaluator.evaluate_skill(skill.id)
                if not decision.allowed:
                    self._audit("policy.skill_denied", skill=skill.id, reason=decision.reason)
                    raise PermissionError(decision.reason)
            model_ref = definition.spec.dependencies.model
            if model_ref is not None:
                decision = self._policy_evaluator.evaluate_model(
                    model_ref.ref, model_ref.model_id, model_ref.provider
                )
                if not decision.allowed:
                    self._audit("policy.model_denied", model=model_ref.ref, reason=decision.reason)
                    raise PermissionError(decision.reason)

        mcp_manager = self._config.mcp_manager
        if mcp_manager is not None:
            await mcp_manager.connect_definition(definition)
            if not agent._internal["mcp_tools_appended"]:
                discovered = mcp_manager.tools()
                adk_tools = [
                    _as_adk_tool(
                        tool,
                        evaluator=self._policy_evaluator,
                        telemetry=self._telemetry,
                        audit=self._config.audit,
                        operation_registry=self._config.operation_registry,
                        side_effect=(agent._internal.get("tool_side_effects") or {}).get(
                            tool.metadata.name
                        ),
                    )
                    for tool in discovered.values()
                ]
                adk_agent = agent._internal["adk_agent"]
                adk_agent.tools.extend(adk_tools)
                agent._internal["mcp_tools_appended"] = True
            agent._internal["mcp_resources"] = {
                ref: (
                    discovery.resources
                    if (discovery := mcp_manager.discovery(ref)) is not None
                    else []
                )
                for ref in [server.ref for server in definition.spec.dependencies.mcp_servers]
            }
            agent._internal["mcp_prompts"] = {
                ref: (
                    discovery.prompts
                    if (discovery := mcp_manager.discovery(ref)) is not None
                    else []
                )
                for ref in [server.ref for server in definition.spec.dependencies.mcp_servers]
            }
            try:
                mcp_healthy = await mcp_manager.health_probe()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("MCP provider failed its health check at startup") from exc
            if not mcp_healthy:
                raise RuntimeError("MCP provider failed its health check at startup")

        agent._internal["started"] = True
        self._telemetry.logger.info("agent started", agent_id=agent.identity.agent_id)

    async def stop(self, agent: RuntimeAgent) -> None:
        """Stop accepting work while leaving runner cleanup to ``close``."""
        agent._internal["started"] = False

    async def shutdown(self, agent: RuntimeAgent) -> None:
        """Release the opaque ADK handle after the agent has stopped."""
        agent._internal = None

    async def close(self) -> None:
        """Close ADK runners and injected provider resources."""
        closed: set[int] = set()
        for runner in self._runners:
            if id(runner) in closed:
                continue
            close = getattr(runner, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            closed.add(id(runner))
        self._runners.clear()
        closed_clients: set[int] = set()
        for client in self._native_model_clients:
            if id(client) in closed_clients:
                continue
            await _close_native_model_client(client)
            closed_clients.add(id(client))
        self._native_model_clients.clear()
        if self._model_provider is not None:
            close = getattr(self._model_provider, "aclose", None)
            if close is not None:
                await close()
        if self._config.mcp_manager is not None:
            await self._config.mcp_manager.aclose()
        memory_provider = self._config.memory_provider
        if memory_provider is not None:
            memory_close = getattr(memory_provider, "aclose", None)
            if memory_close is not None:
                await memory_close()
        if self._config.knowledge_provider is not None:
            knowledge_close = getattr(self._config.knowledge_provider, "aclose", None)
            if knowledge_close is not None:
                await knowledge_close()
        session_provider = self._config.session_provider
        if session_provider is not None:
            session_close = getattr(session_provider, "aclose", None)
            if session_close is not None:
                await _maybe_await(session_close())
        operation_registry = self._config.operation_registry
        if operation_registry is not None:
            await _maybe_await(operation_registry.aclose())

    def health_probes(self) -> dict[str, DependencyProbe]:
        """Expose injected providers as the adapter's active probes."""
        probes: dict[str, DependencyProbe] = {}
        if self._model_provider is not None:
            probes["model"] = self._model_provider.health_check

        memory_provider = self._config.memory_provider

        async def _memory_probe() -> HealthStatus | bool:
            assert memory_provider is not None
            await memory_provider.list_entries()
            return True

        knowledge_provider = self._config.knowledge_provider
        knowledge_refs = list(self._knowledge_refs)

        async def _knowledge_probe() -> HealthStatus | bool:
            assert knowledge_provider is not None
            for source in knowledge_refs:
                if not await knowledge_provider.health_check(source):
                    return False
            return True

        mcp_manager = self._config.mcp_manager

        async def _mcp_probe() -> HealthStatus | bool:
            assert mcp_manager is not None
            return await mcp_manager.health_probe()

        if memory_provider is not None:
            probes["memory"] = _memory_probe
        if knowledge_provider is not None:
            probes["knowledge"] = _knowledge_probe
        if mcp_manager is not None:
            probes["mcp"] = _mcp_probe
        operation_registry = self._config.operation_registry
        if operation_registry is not None:

            async def _operation_probe() -> HealthStatus | bool:
                return cast(
                    HealthStatus | bool,
                    await _maybe_await(operation_registry.health_check()),
                )

            probes["operation_registry"] = _operation_probe
        return probes

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        """Invoke ADK's runner with the verified identity bound."""
        token = set_invocation_identity(
            InvocationIdentity(
                caller=request.caller_identity,
                user=request.user_context,
                workload=self._workload_identity,
            )
        )
        try:
            return await self._invoke(agent, request)
        finally:
            reset_invocation_identity(token)

    async def stream(
        self, agent: RuntimeAgent, request: AgentRequest
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        """Stream provider deltas through ADK's SSE execution mode."""
        if not self.capabilities().streaming:
            raise RuntimeError("Runtime does not support streaming")

        queue: asyncio.Queue[AgentStreamEvent | BaseException | None] = asyncio.Queue()

        async def emit(delta: str) -> None:
            if delta:
                await queue.put(AgentStreamEvent(delta=delta))

        async def run() -> None:
            token = set_invocation_identity(
                InvocationIdentity(
                    caller=request.caller_identity,
                    user=request.user_context,
                    workload=self._workload_identity,
                )
            )
            try:
                response = await self._invoke(
                    agent,
                    request,
                    emit_delta=emit,
                    streaming=True,
                )
                await queue.put(AgentStreamEvent(response=response))
            except BaseException as exc:
                await queue.put(exc)
            finally:
                reset_invocation_identity(token)
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _invoke(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        *,
        emit_delta: Callable[[str], Awaitable[None]] | None = None,
        streaming: bool = False,
    ) -> AgentResponse:
        """Invoke ADK's runner and translate the terminal model event."""
        if agent._internal is None:
            raise RuntimeError("runtime agent has been shut down")
        definition: MicroAgentDefinition = agent._internal["definition"]
        runner = agent._internal["adk_runner"]
        service = agent._internal["adk_session_service"]
        app_name = agent._internal["app_name"]
        user_id = agent._internal["user_id"]
        if request.continuation_id is not None and request.session_id is None:
            raise ContinuationNotFoundError(
                "Google ADK approval continuations require the original session_id"
            )
        checkpoint_store = self._config.checkpoint_store
        bound_identity = get_invocation_identity()
        tenant_id = (
            bound_identity.user.tenant_id
            if bound_identity is not None and bound_identity.user is not None
            else None
        )
        checkpoint: CheckpointRecord | None = None
        checkpoint_id = request.checkpoint_id or request.request_id
        if request.checkpoint_id is not None:
            if checkpoint_store is None:
                raise CheckpointNotFoundError("checkpointing is not configured for this runtime")
            checkpoint = await checkpoint_store.get(request.checkpoint_id, tenant_id=tenant_id)
            if checkpoint is None or checkpoint.agent_id != agent.identity.agent_id:
                raise CheckpointNotFoundError("unknown, expired, or foreign checkpoint")
            if request.input:
                raise ValueError("checkpoint resume must not include new input")
            if not checkpoint.session_id or not checkpoint.request_id:
                raise CheckpointNotFoundError("checkpoint does not contain resumable ADK state")
        session_id = (checkpoint.session_id if checkpoint is not None else None) or (
            request.session_id or str(uuid4())
        )
        trace_id = request.request_id or str(uuid4())
        labels = {"agent": definition.metadata.name}
        span = self._telemetry.start_span(
            "agent.invoke",
            trace_id=trace_id,
            attributes={
                "agent": definition.metadata.name,
                "runtime": "google-adk",
                "session_id": request.session_id,
                "caller_id": (
                    bound_identity.caller.caller_id
                    if bound_identity is not None and bound_identity.caller is not None
                    else None
                ),
            },
        )
        start_time = time.monotonic()

        async def run() -> AgentResponse:
            knowledge_counts: dict[str, int] = {}
            if checkpoint is not None:
                await _restore_adk_checkpoint(
                    service,
                    app_name,
                    user_id,
                    checkpoint,
                )
                content = None
            elif request.continuation_id is not None:
                session = await service.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if session is None or not _has_pending_confirmation(
                    getattr(session, "events", []) or [], request.continuation_id
                ):
                    raise ContinuationNotFoundError("unknown or expired ADK continuation")
                content = _approval_response_content(
                    request.continuation_id,
                    approved=request.approval_decision == "approve",
                )
            else:
                await _ensure_session(service, app_name, user_id, session_id)
                knowledge_context = ""
                if self._config.knowledge_provider is not None and self._knowledge_refs:
                    knowledge_context, knowledge_counts = await retrieve_knowledge_context(
                        self._config.knowledge_provider,
                        build_knowledge_query(request.input),
                        self._knowledge_refs,
                    )
                content = _user_content(request.input, knowledge_context=knowledge_context)
            events: list[Any] = []
            run_kwargs: dict[str, Any] = {
                "user_id": user_id,
                "session_id": session_id,
                "invocation_id": checkpoint.request_id
                if checkpoint is not None
                else request.request_id or None,
            }
            if content is not None:
                run_kwargs["new_message"] = content
            if streaming:
                run_kwargs["run_config"] = _adk_streaming_run_config()
            try:
                async for event in runner.run_async(**run_kwargs):
                    events.append(event)
                    if emit_delta is not None and getattr(event, "partial", False):
                        await emit_delta(_event_text(event))
            except BaseException:
                if checkpoint_store is not None and request.continuation_id is None:
                    await _checkpoint_failed_adk_invocation(
                        checkpoint_store,
                        service,
                        app_name,
                        user_id,
                        agent,
                        checkpoint_id=checkpoint_id,
                        request_id=checkpoint.request_id
                        if checkpoint is not None
                        else request.request_id,
                        session_id=session_id,
                        input_payload=(
                            checkpoint.input_payload if checkpoint is not None else request.input
                        ),
                        tenant_id=tenant_id,
                        yielded_events=events,
                    )
                raise
            await self._auto_store(agent, service, app_name, user_id, session_id)
            approval = _approval_metadata(events)
            if approval is not None:
                if checkpoint_store is not None:
                    await checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
                pending_tools = list(approval["pending_tools"])
                continuation_id = str(approval["continuation_id"])
                self._audit(
                    "approval.requested",
                    agent=definition.metadata.name,
                    tools=pending_tools,
                    continuation_id=continuation_id,
                )
                return AgentResponse(
                    output={
                        "content": "",
                        "tool_results": _tool_results_from_events(events),
                    },
                    request_id=request.request_id,
                    session_id=session_id,
                    status="approval_required",
                    metadata={
                        "runtime": "google-adk",
                        "event_count": len(events),
                        "continuation_id": continuation_id,
                        "pending_tools": pending_tools,
                        "approval_hints": approval["approval_hints"],
                        "approval_payloads": approval["approval_payloads"],
                    },
                )
            output = _terminal_text(events, agent._internal["adk_agent"].name)
            if checkpoint_store is not None:
                await checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
            return AgentResponse(
                output={
                    "content": output,
                    "tool_results": _tool_results_from_events(events),
                },
                request_id=request.request_id,
                session_id=session_id,
                status="success",
                metadata={
                    "runtime": "google-adk",
                    "event_count": len(events),
                    "knowledge_entries": sum(knowledge_counts.values())
                    if request.continuation_id is None
                    else 0,
                    "knowledge_sources": knowledge_counts
                    if request.continuation_id is None
                    else {},
                    "resumed_from_checkpoint": request.checkpoint_id,
                },
            )

        timeout = _shortest_timeout(
            definition.spec.runtime.timeout_seconds,
            request.timeout_seconds,
        )
        try:
            if timeout is None:
                response = await run()
            else:
                response = await asyncio.wait_for(run(), timeout=timeout)
        except Exception:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            self._telemetry.logger.error(
                "invocation failed",
                request_id=request.request_id,
                session_id=request.session_id,
            )
            self._telemetry.finish_span(span)
            raise
        latency_ms = round((time.monotonic() - start_time) * 1000, 2)
        span.set_attribute("latency_ms", latency_ms)
        span.set_attribute("event_count", response.metadata.get("event_count", 0))
        self._telemetry.finish_span(span)
        self._telemetry.increment("agent_invocations_total", labels)
        self._telemetry.record("agent_invocation_latency_ms", latency_ms, labels)
        return response

    async def _auto_store(
        self,
        agent: RuntimeAgent,
        service: Any,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """Persist the session into ADK memory when auto-store is enabled."""
        memory_service = agent._internal["adk_memory_service"]
        if memory_service is None or not self._config.effective_memory_policy.auto_store:
            return
        session = await service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is not None:
            await memory_service.add_session_to_memory(session)

    def _audit(self, event: str, **fields: Any) -> None:
        if self._config.audit is not None:
            self._config.audit.record(event, **fields)

    def _build_memory_service(self) -> Any:
        """Construct the ADK memory service for the declared memory dependency."""
        if self._config.memory_service_factory is not None:
            return self._config.memory_service_factory()
        if self._config.memory_provider is None:
            return None
        return _provider_memory_service(self._config.memory_provider)

    def _adk_model(self, definition: MicroAgentDefinition, config: ModelConfig) -> Any:
        if self._config.model_factory is not None:
            return self._config.model_factory(definition)
        if self._model_provider is not None:
            return _provider_model(self._model_provider, config)
        model = config.model_id or config.ref
        provider = (config.provider or "").lower()
        if provider and provider not in {"google", "gemini", "google-genai"}:
            raise GoogleAdkError(
                "Google ADK requires a Google model or an injected model_factory/provider; "
                f"provider '{config.provider}' is not a native ADK model"
            )
        if self._config.model_api_key is not None:
            try:
                from google.adk.models import Gemini
                from google.genai import Client
            except ImportError as exc:  # pragma: no cover - guarded by _load_adk
                raise GoogleAdkError("Google ADK credential APIs are unavailable") from exc
            client = Client(api_key=self._config.model_api_key)
            self._native_model_clients.append(client)
            return Gemini(model=model, client=client)
        return model

    def _resolve_tools(self, definition: MicroAgentDefinition) -> dict[str, Tool]:
        """Resolve declared tools against the configured and built-in registries."""
        registry = builtin_tool_registry()
        registry.update(load_plugin_tools())
        registry.update(self._config.tool_registry)
        tools: dict[str, Tool] = {}
        for tool_definition in definition.spec.dependencies.tools:
            if tool_definition.name in tools:
                continue
            tool = registry.get(tool_definition.name)
            if tool is not None:
                tools[tool_definition.name] = tool
        return tools


def _load_adk() -> tuple[Any, Any, Any]:
    try:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise GoogleAdkError(
            "Google ADK is not installed; install the optional 'adk' extra"
        ) from exc
    return LlmAgent, Runner, InMemorySessionService


def _adk_resumability_config() -> Any:
    """Return ADK's resumability setting for checkpoint-backed runners."""
    try:
        from google.adk.apps import ResumabilityConfig
    except ImportError as exc:  # pragma: no cover - guarded by _load_adk
        raise GoogleAdkError("Google ADK resumability APIs are unavailable") from exc
    return ResumabilityConfig(is_resumable=True)


async def _restore_adk_checkpoint(
    service: Any,
    app_name: str,
    user_id: str,
    checkpoint: CheckpointRecord,
) -> None:
    """Restore a checkpoint snapshot into the ADK session service."""
    state = checkpoint.runtime_state.get("google_adk")
    if not isinstance(state, dict) or state.get("version") != 1:
        raise CheckpointNotFoundError("checkpoint does not contain resumable ADK state")
    serialized_events = state.get("events")
    if not isinstance(serialized_events, list) or not serialized_events:
        raise CheckpointNotFoundError("checkpoint does not contain resumable ADK events")

    try:
        from google.adk.events import Event

        events = [Event.model_validate(event) for event in serialized_events]
    except (ImportError, TypeError, ValueError) as exc:
        raise CheckpointNotFoundError("checkpoint contains invalid ADK event state") from exc

    existing = await service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=checkpoint.session_id,
    )
    if existing is not None:
        await service.delete_session(
            app_name=app_name,
            user_id=user_id,
            session_id=checkpoint.session_id,
        )

    session = await service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=checkpoint.session_id,
        state=state.get("session_state") if isinstance(state.get("session_state"), dict) else None,
    )
    for event in events:
        await service.append_event(session=session, event=event)


async def _checkpoint_failed_adk_invocation(
    checkpoint_store: CheckpointStore,
    service: Any,
    app_name: str,
    user_id: str,
    agent: RuntimeAgent,
    *,
    checkpoint_id: str,
    request_id: str,
    session_id: str,
    input_payload: dict[str, Any],
    tenant_id: str | None,
    yielded_events: list[Any],
) -> None:
    """Persist a replayable ADK snapshot after a failed invocation.

    This is deliberately best effort from an exception handler: a checkpoint
    storage failure must never hide the original model/tool error. Pending
    non-read-only tool calls invalidate the snapshot because ADK resumption is
    at-least-once for tools.
    """
    try:
        session = await service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        events = list(getattr(session, "events", []) or []) if session is not None else []
        if not events:
            events = list(yielded_events)
        if not events or _adk_has_unsafe_pending_tool(agent, events, request_id):
            await checkpoint_store.delete(checkpoint_id, tenant_id=tenant_id)
            return

        serialized_events = [event.model_dump(mode="json") for event in events]
        session_state = getattr(session, "state", {}) if session is not None else {}
        runtime_state = {
            "google_adk": {
                "version": 1,
                "events": serialized_events,
                "session_state": dict(session_state) if isinstance(session_state, dict) else {},
            }
        }
        await checkpoint_store.save(
            CheckpointRecord(
                checkpoint_id=checkpoint_id,
                agent_id=agent.identity.agent_id,
                request_id=request_id,
                session_id=session_id,
                input_payload=dict(input_payload),
                messages=_checkpoint_messages_from_adk_events(events),
                all_tool_results=_tool_results_from_events(events),
                history_tail_length=len(events),
                tenant_id=tenant_id,
                runtime_state=runtime_state,
            )
        )
    except BaseException:
        # Checkpointing is recovery assistance; it must not mask the failure
        # that caused the invocation to stop.
        return


def _adk_has_unsafe_pending_tool(
    agent: RuntimeAgent,
    events: list[Any],
    invocation_id: str,
) -> bool:
    """Return true when resumption could repeat a non-read-only tool call."""
    calls: dict[str, str] = {}
    responses: set[str] = set()
    for event in events:
        if getattr(event, "invocation_id", None) != invocation_id:
            continue
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", []) or []:
            function_call = getattr(part, "function_call", None)
            if function_call is not None and getattr(function_call, "id", None):
                name = str(getattr(function_call, "name", ""))
                if name != _ADK_REQUEST_CONFIRMATION_NAME:
                    calls[str(function_call.id)] = name
            function_response = getattr(part, "function_response", None)
            if function_response is not None and getattr(function_response, "id", None):
                responses.add(str(function_response.id))

    return any(
        not _adk_tool_is_replay_safe(agent, name)
        for call_id, name in calls.items()
        if call_id not in responses
    )


def _adk_tool_is_replay_safe(agent: RuntimeAgent, name: str) -> bool:
    """Resolve an ADK tool name to a fail-closed read-only declaration."""
    side_effects = agent._internal.get("tool_side_effects") or {}
    classification = side_effects.get(name)
    if classification is None:
        for declared_name, declared_classification in side_effects.items():
            if re.sub(r"[^A-Za-z0-9_]", "_", str(declared_name)) == name:
                classification = declared_classification
                break
    if classification is not None:
        return str(classification) == "read_only"

    for tool in agent._internal.get("adk_agent").tools or []:
        if getattr(tool, "name", None) == name:
            return str(getattr(tool, "_side_effect", "unsafe")) == "read_only"
    return False


def _checkpoint_messages_from_adk_events(events: list[Any]) -> list[dict[str, Any]]:
    """Provide a runtime-neutral transcript alongside opaque ADK state."""
    messages: list[dict[str, Any]] = []
    for event in events:
        text = _event_text(event)
        author = str(getattr(event, "author", "") or "assistant")
        message: dict[str, Any] = {
            "role": "assistant" if author not in {"user", "tool"} else author,
            "content": text,
        }
        if text or message["role"] in {"user", "tool"}:
            messages.append(message)
    return messages


def _provider_memory_service(provider: MemoryProvider) -> Any:
    """Build an ADK ``BaseMemoryService`` bridge over the Micro-Agent provider."""
    try:
        from google.adk.memory.base_memory_service import (
            BaseMemoryService,
            SearchMemoryResponse,
        )
        from google.adk.memory.memory_entry import MemoryEntry as AdkMemoryEntry
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google ADK memory APIs are unavailable") from exc

    class ProviderMemoryService(BaseMemoryService):
        async def add_session_to_memory(self, session: Any) -> None:
            session_id = str(getattr(session, "id", "") or uuid4())
            for index, event in enumerate(getattr(session, "events", []) or []):
                text = _event_text(event)
                if not text:
                    continue
                await provider.store(
                    MemoryEntry(
                        key=f"{session_id}:{index}",
                        value={"text": text, "author": getattr(event, "author", "")},
                        scope="agent",
                    )
                )

        async def search_memory(self, *, app_name: str, user_id: str, query: str) -> Any:
            entries = await provider.search(query)
            memories = [
                AdkMemoryEntry(
                    content=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=_entry_text(entry))],
                    ),
                    custom_metadata={"key": entry.key, "scope": entry.scope},
                )
                for entry in entries
            ]
            return SearchMemoryResponse(memories=memories)

    return ProviderMemoryService()


def _provider_session_service(provider: SessionProvider) -> Any:
    """Build the ADK session service over a configured state provider."""
    if provider is None:  # pragma: no cover - guarded by the caller
        raise GoogleAdkError("a session provider is required")
    from runtimes.google_adk.session_service import build_adk_session_service

    return build_adk_session_service(provider)


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    return "".join(
        str(part.text)
        for part in (getattr(content, "parts", []) or [])
        if getattr(part, "text", None)
    )


def _entry_text(entry: MemoryEntry) -> str:
    value = entry.value
    if isinstance(value, dict):
        return str(value.get("text", ""))
    return str(value)


def _provider_model(provider: ModelProvider, config: ModelConfig) -> Any:
    """Build an ADK ``BaseLlm`` bridge around the Micro-Agent provider SPI."""
    try:
        from google.adk.models import BaseLlm
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types
        from pydantic import PrivateAttr
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google ADK model APIs are unavailable") from exc

    class ProviderLlm(BaseLlm):
        _provider: ModelProvider = PrivateAttr()
        _config: ModelConfig = PrivateAttr()

        def __init__(self, model_provider: ModelProvider, model_config: ModelConfig) -> None:
            super().__init__(model=model_config.model_id or model_config.ref)
            self._provider = model_provider
            self._config = model_config

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator[Any, None]:
            messages = _messages_from_adk(llm_request)
            tools = _tools_from_adk(llm_request)
            if stream and self._provider.capabilities().streaming:
                final_response = None
                async for stream_event in self._provider.stream(
                    self._config, messages, tools=tools or None
                ):
                    if stream_event.delta:
                        yield _llm_response(
                            stream_event.delta,
                            None,
                            types=types,
                            llm_response_cls=LlmResponse,
                            partial=True,
                        )
                    if stream_event.response is not None:
                        final_response = stream_event.response
                if final_response is None:
                    raise GoogleAdkError("model provider stream ended without a final response")
                yield _llm_response(
                    "",
                    final_response,
                    types=types,
                    llm_response_cls=LlmResponse,
                    partial=False,
                )
                return

            response = await self._provider.generate(self._config, messages, tools=tools or None)
            yield _llm_response(
                "",
                response,
                types=types,
                llm_response_cls=LlmResponse,
                partial=False,
            )

    return ProviderLlm(provider, config)


def _llm_response(
    delta: str,
    response: Any,
    *,
    types: Any,
    llm_response_cls: Any,
    partial: bool,
) -> Any:
    """Translate one runtime-neutral model event into an ADK response."""
    parts: list[Any] = []
    if delta:
        parts.append(types.Part.from_text(text=delta))
    if response is not None:
        if response.content:
            parts.append(types.Part.from_text(text=response.content))
        for tool_request in response.tool_requests:
            call_id = str(tool_request.get("id") or f"micro-agent-call-{uuid4()}")
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=call_id,
                        name=str(tool_request.get("name", "")),
                        args=dict(tool_request.get("arguments") or {}),
                    )
                )
            )
    return llm_response_cls(
        content=types.Content(role="model", parts=parts),
        finish_reason=cast(Any, getattr(response, "finish_reason", "stop")),
        partial=partial,
    )


def _adk_streaming_run_config() -> Any:
    """Build the optional ADK run configuration that enables SSE output."""
    try:
        from google.adk.agents._streaming_mode import StreamingMode
        from google.adk.agents.run_config import RunConfig
    except ImportError as exc:  # pragma: no cover - guarded by _load_adk
        raise GoogleAdkError("Google ADK streaming APIs are unavailable") from exc
    return RunConfig(streaming_mode=StreamingMode.SSE)


async def _close_native_model_client(client: Any) -> None:
    """Close both sync and async transports owned by a native ADK client."""
    close = getattr(client, "close", None)
    if close is not None:
        result = close()
        if hasattr(result, "__await__"):
            await result
    async_client = getattr(client, "aio", None)
    aclose = getattr(async_client, "aclose", None)
    if aclose is not None:
        result = aclose()
        if hasattr(result, "__await__"):
            await result


def _messages_from_adk(llm_request: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for content in getattr(llm_request, "contents", []) or []:
        role = (
            "assistant"
            if getattr(content, "role", "user") == "model"
            else getattr(content, "role", "user")
        )
        parts = getattr(content, "parts", []) or []
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
        ]
        if tool_responses:
            messages.extend(tool_responses)
        elif tool_calls:
            messages.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        else:
            messages.append({"role": role, "content": text})
    return messages


def _tools_from_adk(llm_request: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for name, tool in (getattr(llm_request, "tools_dict", {}) or {}).items():
        declaration = getattr(tool, "_get_declaration", lambda: None)()
        parameters = getattr(declaration, "parameters_json_schema", None)
        if parameters is None:
            schema = getattr(declaration, "parameters", None)
            dump = getattr(schema, "model_dump", None)
            parameters = dump(mode="json") if callable(dump) else {}
        tools.append(
            {
                "name": name,
                "description": getattr(tool, "description", ""),
                "input_schema": parameters or {},
            }
        )
    return tools


def _as_adk_tool(
    tool: Tool,
    *,
    evaluator: PolicyEvaluator | None = None,
    telemetry: Telemetry | None = None,
    audit: AuditSink | None = None,
    operation_registry: OperationRegistryProtocol | None = None,
    side_effect: str | None = None,
) -> Any:
    """Adapt a Micro-Agent tool to ADK's client-side ``BaseTool`` contract.

    When a policy evaluator is configured, every execution is checked against
    the tool and side-effect policies before the tool runs — deterministic
    enforcement that prompt instructions cannot override.
    """
    try:
        from google.adk.tools.base_tool import BaseTool
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google ADK tool APIs are unavailable") from exc

    class AdkToolAdapter(BaseTool):
        def __init__(self, micro_tool: Tool) -> None:
            super().__init__(
                name=re.sub(r"[^A-Za-z0-9_]", "_", micro_tool.metadata.name),
                description=micro_tool.metadata.description or micro_tool.metadata.name,
            )
            self._micro_tool = micro_tool
            self._side_effect = normalize_tool_side_effect(
                side_effect or micro_tool.metadata.side_effect
            )

        def _get_declaration(self) -> Any:
            schema = self._micro_tool.input_schema.parameters
            return types.FunctionDeclaration(
                name=self.name,
                description=self.description,
                parameters=cast(Any, schema or None),
            )

        def _denied(self, reason: str, event: str = "policy.tool_denied") -> dict[str, Any]:
            tool_name = self._micro_tool.metadata.name
            if telemetry is not None:
                telemetry.increment("policy_denials_total", {"tool": tool_name})
                telemetry.logger.warning("tool denied by policy", tool=tool_name, reason=reason)
            if audit is not None:
                audit.record(event, tool=tool_name, reason=reason)
            return {"error": reason}

        async def check_require_confirmation(self, args: dict[str, Any], tool_context: Any) -> bool:
            """Expose Micro-Agent approval policy to ADK's resume validator."""
            if evaluator is None or self._side_effect == "read_only":
                return False
            return evaluator.evaluate_side_effect(self._micro_tool.metadata.name).requires_approval

        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
            if evaluator is not None:
                decision = evaluator.evaluate_tool(self._micro_tool.metadata.name)
                if not decision.allowed:
                    return self._denied(f"denied by policy: {decision.reason}")
                if self._side_effect != "read_only":
                    side_effect_decision = evaluator.evaluate_side_effect(
                        self._micro_tool.metadata.name
                    )
                else:
                    side_effect_decision = None
                if side_effect_decision is not None and not side_effect_decision.allowed:
                    if side_effect_decision.requires_approval:
                        confirmation = getattr(tool_context, "tool_confirmation", None)
                        if confirmation is None:
                            tool_context.request_confirmation(
                                hint=(
                                    f"Approve the side-effect tool call "
                                    f"{self._micro_tool.metadata.name}"
                                ),
                                payload={"tool": self._micro_tool.metadata.name},
                            )
                            tool_context.actions.skip_summarization = True
                            if telemetry is not None:
                                telemetry.increment(
                                    "approval_requests_total",
                                    {"tool": self._micro_tool.metadata.name},
                                )
                            return {
                                "error": (
                                    "This tool call requires confirmation, "
                                    "please approve or reject."
                                )
                            }
                        if not getattr(confirmation, "confirmed", False):
                            return self._denied(
                                "denied by caller: approval denied",
                                event="approval.denied",
                            )
                        if audit is not None:
                            audit.record(
                                "approval.granted",
                                tool=self._micro_tool.metadata.name,
                            )
                    else:
                        return self._denied(
                            f"denied by side-effect policy: {side_effect_decision.reason}",
                            event="policy.side_effect_denied",
                        )
            operation: Operation | None = None
            registry = operation_registry
            if registry is not None and self._side_effect != "read_only":
                identity = get_invocation_identity()
                tenant_id = (
                    identity.user.tenant_id
                    if identity is not None and identity.user is not None
                    else None
                )
                operation = Operation(
                    name=self._micro_tool.metadata.name,
                    arguments=args,
                    idempotency_key=(
                        str(args["idempotency_key"])
                        if args.get("idempotency_key") is not None
                        else None
                    ),
                    tenant_id=tenant_id,
                    retry_classification={
                        "idempotent": RetryClassification.IDEMPOTENT,
                        "unsafe": RetryClassification.UNSAFE,
                    }.get(self._side_effect, RetryClassification.SAFE),
                )
                claimed, prior = await _maybe_await(registry.claim(operation))
                if not claimed:
                    return {
                        "output": prior.output if prior is not None else None,
                        "error": (
                            "operation is already in progress"
                            if prior is not None and prior.status == "in_progress"
                            else (prior.error if prior is not None else None)
                        ),
                        "was_deduplicated": True,
                    }
            try:
                result = await self._micro_tool.execute(args)
            except Exception as exc:  # noqa: BLE001 — preserve operation outcome
                if operation is not None and registry is not None:
                    await _maybe_await(
                        registry.record(
                            operation,
                            OperationResult(
                                operation_id=operation.operation_id,
                                status="failed",
                                error=str(exc),
                            ),
                        )
                    )
                raise
            if operation is not None and registry is not None:
                await _maybe_await(
                    registry.record(
                        operation,
                        OperationResult(
                            operation_id=operation.operation_id,
                            status="failed" if result.is_error else "success",
                            output=result.output,
                            error=result.error,
                        ),
                    )
                )
            if result.is_error:
                return {"error": result.error or f"tool '{self.name}' failed"}
            return result.output

    return AdkToolAdapter(tool)


async def _ensure_session(service: Any, app_name: str, user_id: str, session_id: str) -> None:
    session = await service.get_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )
    if session is None:
        await service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )


def _user_content(payload: dict[str, Any], *, knowledge_context: str = "") -> Any:
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google GenAI content types are unavailable") from exc
    payload_text = json.dumps(payload, default=str)
    if knowledge_context:
        payload_text = f"{knowledge_context}\n\nUser input:\n{payload_text}"
    return types.Content(
        role="user",
        parts=[types.Part.from_text(text=payload_text)],
    )


def _approval_response_content(continuation_id: str, *, approved: bool) -> Any:
    """Build ADK's native user function response for a confirmation resume."""
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google GenAI content types are unavailable") from exc
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=continuation_id,
                    name=_ADK_REQUEST_CONFIRMATION_NAME,
                    response={"confirmed": approved},
                )
            )
        ],
    )


def _has_pending_confirmation(events: list[Any], continuation_id: str) -> bool:
    """Check that an ADK confirmation call exists and was not already answered."""
    found = False
    answered = False
    for event in events:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", []) or []:
            function_call = getattr(part, "function_call", None)
            if (
                function_call is not None
                and function_call.name == _ADK_REQUEST_CONFIRMATION_NAME
                and function_call.id == continuation_id
            ):
                found = True
            function_response = getattr(part, "function_response", None)
            if function_response is not None and function_response.id == continuation_id:
                answered = True
    return found and not answered


def _approval_metadata(events: list[Any]) -> dict[str, Any] | None:
    """Extract a runtime-neutral approval continuation from ADK events."""
    requested: dict[str, Any] = {}
    for event in events:
        actions = getattr(event, "actions", None)
        confirmations = getattr(actions, "requested_tool_confirmations", None) or {}
        requested.update(confirmations)
    if not requested:
        return None

    generated: dict[str, dict[str, Any]] = {}
    for event in events:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", []) or []:
            function_call = getattr(part, "function_call", None)
            if function_call is None or function_call.name != _ADK_REQUEST_CONFIRMATION_NAME:
                continue
            args = getattr(function_call, "args", None) or {}
            original = args.get("originalFunctionCall")
            if not isinstance(original, dict) or not original.get("id"):
                continue
            if not function_call.id:
                continue
            tool_confirmation = args.get("toolConfirmation") or {}
            generated[str(original["id"])] = {
                "continuation_id": str(function_call.id),
                "tool": str(original.get("name", "")),
                "hint": tool_confirmation.get("hint", ""),
                "payload": tool_confirmation.get("payload"),
            }

    pending_tools: list[str] = []
    approval_hints: dict[str, str] = {}
    approval_payloads: dict[str, Any] = {}
    continuation_id: str | None = None
    for original_id, _confirmation in requested.items():
        details = generated.get(str(original_id))
        if details is None:
            continue
        continuation_id = continuation_id or details["continuation_id"]
        tool_name = details["tool"]
        pending_tools.append(tool_name)
        approval_hints[tool_name] = details["hint"]
        approval_payloads[tool_name] = details["payload"]

    if continuation_id is None:
        return None
    return {
        "continuation_id": continuation_id,
        "pending_tools": pending_tools,
        "approval_hints": approval_hints,
        "approval_payloads": approval_payloads,
    }


def _terminal_text(events: list[Any], agent_name: str) -> str:
    for event in reversed(events):
        if getattr(event, "author", None) != agent_name:
            continue
        text = _event_text(event)
        if text:
            return text
    return ""


def _tool_results_from_events(events: list[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in events:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", []) or []:
            response = getattr(part, "function_response", None)
            if response is None:
                continue
            results.append(
                {
                    "tool": getattr(response, "name", ""),
                    "output": getattr(response, "response", {}) or {},
                    "tool_call_id": getattr(response, "id", None),
                }
            )
    return results


def _adk_name(name: str) -> str:
    """Map a DNS-compatible definition name to ADK's identifier requirement."""
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not candidate or not candidate[0].isalpha() and candidate[0] != "_":
        candidate = f"agent_{candidate}"
    return f"micro_agent_{candidate}"


def _shortest_timeout(*values: float | int | None) -> float | None:
    configured = [float(value) for value in values if value is not None]
    return min(configured) if configured else None


__all__ = ["GoogleAdkError", "GoogleAdkRuntime", "GoogleAdkRuntimeConfig"]
