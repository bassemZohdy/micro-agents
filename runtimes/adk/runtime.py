"""Custom runtime loop in the legacy ADK-named package.

The executable bootstrap selects the fake or OpenAI-compatible model provider
and built-in state providers from resolved configuration. This module does not
integrate Google ADK, and no runtime-native types leak into definition or core
contracts.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    ContinuationNotFoundError,
)
from micro_agent.definition import ErrorPolicy, MicroAgentDefinition
from micro_agent.health import DependencyProbe, HealthStatus
from micro_agent.knowledge import KnowledgeRetriever, KnowledgeSource
from micro_agent.mcp import McpConnectionManager
from micro_agent.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelProvider,
)
from micro_agent.observability import AuditSink, Telemetry
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.security import (
    AgentPolicy,
    ApprovalStore,
    InMemoryApprovalStore,
    InvocationIdentity,
    Operation,
    OperationRegistryProtocol,
    OperationResult,
    PendingApproval,
    PolicyEvaluator,
    build_security_context,
    get_invocation_identity,
    reset_invocation_identity,
    resolve_workload_identity,
    set_invocation_identity,
)
from micro_agent.session import SessionProvider
from micro_agent.tools import Tool, builtin_tool_registry


class _ApprovalPausedError(Exception):
    """Internal marker: the invocation paused waiting for an approval."""

    def __init__(self, approval: PendingApproval) -> None:
        self.approval = approval


_DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
_MAX_SESSION_HISTORY_MESSAGES = 20
_T = TypeVar("_T")


async def _maybe_await(value: _T | Awaitable[_T]) -> _T:
    """Normalize sync local providers and async external providers."""
    if inspect.isawaitable(value):
        return await value
    return value


def _call_with_supported_kwargs(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Call an optional provider extension without breaking legacy providers."""
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return method(*args, **kwargs)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return method(*args, **kwargs)
    supported = {name: value for name, value in kwargs.items() if name in parameters}
    return method(*args, **supported)


@dataclass(frozen=True)
class _InvocationDeadline:
    """Absolute monotonic deadline shared by every invocation operation."""

    expires_at: float | None

    @classmethod
    def from_seconds(cls, *timeouts: float | int | None) -> _InvocationDeadline:
        """Build a deadline from the shortest configured timeout."""
        configured = [float(value) for value in timeouts if value is not None]
        if not configured:
            return cls(None)
        return cls(time.monotonic() + min(configured))

    def remaining(self) -> float | None:
        """Return remaining seconds, or ``None`` when no deadline is set."""
        if self.expires_at is None:
            return None
        return self.expires_at - time.monotonic()

    @property
    def expired(self) -> bool:
        """Whether the deadline has elapsed."""
        remaining = self.remaining()
        return remaining is not None and remaining <= 0

    async def run(self, operation: Awaitable[_T], cap: float | int | None = None) -> _T:
        """Await an operation using the remaining budget and an optional cap."""
        remaining = self.remaining()
        if cap is not None:
            remaining = min(float(cap), remaining) if remaining is not None else float(cap)
        if remaining is None:
            return await operation
        if remaining <= 0:
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise TimeoutError("invocation deadline exceeded")
        try:
            return await asyncio.wait_for(operation, timeout=remaining)
        except TimeoutError as exc:
            if self.expired:
                raise TimeoutError("invocation deadline exceeded") from exc
            raise


@dataclass
class AdkRuntimeConfig:
    """Configuration for the ADK runtime."""

    fake_model_config: FakeModelConfig = field(default_factory=FakeModelConfig)
    model_provider: ModelProvider | None = None
    session_provider: SessionProvider | None = None
    memory_provider: MemoryProvider | None = None
    memory_policy: MemoryPolicy | None = None
    knowledge_provider: KnowledgeRetriever | None = None
    mcp_manager: McpConnectionManager | None = None
    policy: AgentPolicy | None = None
    audit: AuditSink | None = None
    approval_store: ApprovalStore | None = None
    operation_registry: OperationRegistryProtocol | None = None
    telemetry: Telemetry | None = None
    tool_registry: dict[str, Tool] | None = None
    default_max_iterations: int = 5


class AdkRuntime(AgentRuntime):
    """ADK runtime implementation.

    Executes a bounded agent loop (model call -> tool execution -> ...) with
    RuntimeSemantics enforcement (overall timeout, max iterations, error
    policy), session and memory integration, and telemetry (spans, metrics,
    structured logs). ADK types do not leak through the public interface.
    """

    def __init__(self, config: AdkRuntimeConfig | None = None) -> None:
        self._config = config or AdkRuntimeConfig()
        self._model_provider: ModelProvider = (
            self._config.model_provider
            if self._config.model_provider is not None
            else FakeModelProvider(self._config.fake_model_config)
        )
        self._telemetry = self._config.telemetry or Telemetry.from_environment()
        self._policy_evaluator = (
            PolicyEvaluator(self._config.policy) if self._config.policy is not None else None
        )
        self._approval_store = self._config.approval_store or InMemoryApprovalStore()
        self._workload_identity = resolve_workload_identity()
        self._knowledge_refs: list[KnowledgeSource] = []
        self._started = False

    # ------------------------------------------------------------------
    # Capabilities and lifecycle
    # ------------------------------------------------------------------

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            streaming=False,
            memory=self._config.memory_provider is not None,
            mcp=self._config.mcp_manager is not None,
            a2a=False,
            structured_output=False,
        )

    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        identity = AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        )
        capabilities = AgentCapabilities(memory=self._config.memory_provider is not None)

        tools, unresolved = self._resolve_tools(definition)
        if unresolved:
            self._telemetry.logger.warning(
                "unresolved tools (no native implementation or MCP binding)",
                agent_id=identity.agent_id,
                tools=unresolved,
            )

        session_ttl = definition.spec.dependencies.session.ttl_seconds
        memory_scope = (
            definition.spec.dependencies.memory.scope
            if definition.spec.dependencies.memory
            else None
        )
        skills = definition.spec.dependencies.skills
        self._knowledge_refs = [
            KnowledgeSource(ref=ref.ref, source_type=ref.source_type, version=ref.version)
            for ref in definition.spec.dependencies.knowledge
        ]

        self._telemetry.logger.set_context(
            agent_id=identity.agent_id,
            agent_version=identity.agent_version,
        )

        return RuntimeAgent(
            identity=identity,
            capabilities=capabilities,
            _internal={
                "definition": definition,
                "model_provider": self._model_provider,
                "tools": tools,
                "unresolved_tools": unresolved,
                "session_provider": self._config.session_provider,
                "session_ttl": session_ttl,
                "memory_provider": self._config.memory_provider,
                "memory_scope": memory_scope or "agent",
                "skills": skills,
                "security_context": build_security_context(definition),
                "started": False,
            },
        )

    async def start(self, agent: RuntimeAgent) -> None:
        try:
            healthy = await self._model_provider.health_check()
        except Exception as exc:  # noqa: BLE001 — startup errors become stable runtime failures
            raise RuntimeError("model provider failed its health check at startup") from exc
        if not healthy:
            raise RuntimeError("model provider failed its health check at startup")
        definition: MicroAgentDefinition = agent._internal["definition"]

        # State providers are required dependencies when configured. Probe
        # them before marking the agent ready so a broken store cannot be
        # discovered only on the first invocation.
        session_provider = self._config.session_provider
        if session_provider is not None:
            try:
                await session_provider.list_active()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("session provider failed its health check at startup") from exc

        memory_provider = self._config.memory_provider
        if memory_provider is not None:
            try:
                await memory_provider.list_entries()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("memory provider failed its health check at startup") from exc

        operation_registry = self._config.operation_registry
        if operation_registry is not None:
            try:
                healthy = await _maybe_await(operation_registry.health_check())
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("operation registry failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("operation registry failed its health check at startup")

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

        # Capability negotiation: declaring tools against a provider that
        # cannot call them must fail at startup, not silently drop them.
        declared_tools = definition.spec.dependencies.tools
        if declared_tools and not self._model_provider.capabilities().tool_use:
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
            agent._internal["tools"].update(mcp_manager.tools())
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
        self._started = True
        self._telemetry.logger.info(
            "agent started",
            agent_id=agent.identity.agent_id,
        )

    async def stop(self, agent: RuntimeAgent) -> None:
        agent._internal["started"] = False
        self._started = False
        self._telemetry.logger.info("agent stopped", agent_id=agent.identity.agent_id)

    async def shutdown(self, agent: RuntimeAgent) -> None:
        agent._internal = None

    async def close(self) -> None:
        """Release runtime-level resources (model, state, knowledge, and MCP)."""
        aclose = getattr(self._model_provider, "aclose", None)
        if aclose is not None:
            await _maybe_await(aclose())
        for provider in (
            self._config.session_provider,
            self._config.memory_provider,
            self._config.operation_registry,
        ):
            provider_close = getattr(provider, "aclose", None)
            if provider_close is not None:
                await _maybe_await(provider_close())
        if self._config.knowledge_provider is not None:
            knowledge_close = getattr(self._config.knowledge_provider, "aclose", None)
            if knowledge_close is not None:
                await _maybe_await(knowledge_close())
        if self._config.mcp_manager is not None:
            await self._config.mcp_manager.aclose()

    def health_probes(self) -> dict[str, DependencyProbe]:
        """Active dependency probes for the HealthChecker."""
        probes: dict[str, DependencyProbe] = {"model": self._model_provider.health_check}

        async def _session_probe() -> HealthStatus | bool:
            assert self._config.session_provider is not None
            await self._config.session_provider.list_active()
            return True

        async def _memory_probe() -> HealthStatus | bool:
            assert self._config.memory_provider is not None
            await self._config.memory_provider.list_entries()
            return True

        knowledge_provider = self._config.knowledge_provider
        knowledge_refs = list(self._knowledge_refs)

        async def _knowledge_probe() -> HealthStatus | bool:
            assert knowledge_provider is not None
            for source in knowledge_refs:
                if not await knowledge_provider.health_check(source):
                    return False
            return True

        async def _mcp_probe() -> HealthStatus | bool:
            assert self._config.mcp_manager is not None
            return await self._config.mcp_manager.health_probe()

        if self._config.session_provider is not None:
            probes["session"] = _session_probe
        if self._config.memory_provider is not None:
            probes["memory"] = _memory_probe
        operation_registry = self._config.operation_registry

        async def _operation_probe() -> HealthStatus | bool:
            assert operation_registry is not None
            return await _maybe_await(operation_registry.health_check())

        if operation_registry is not None:
            probes["operation_registry"] = _operation_probe
        if knowledge_provider is not None:
            probes["knowledge"] = _knowledge_probe
        if self._config.mcp_manager is not None:
            probes["mcp"] = _mcp_probe
        return probes

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        """Invoke with the verified identity bound for every downstream operation."""
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

    async def _invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        semantics = definition.spec.runtime
        trace_id = request.request_id or str(uuid4())
        labels = {"agent": definition.metadata.name}
        deadline = _InvocationDeadline.from_seconds(
            semantics.timeout_seconds,
            request.timeout_seconds,
        )

        async def run_once() -> AgentResponse:
            return await self._invoke_inner(agent, request, trace_id, deadline)

        start_time = time.monotonic()
        try:
            response = await run_once()
        except TimeoutError:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            self._telemetry.logger.error(
                "invocation deadline exceeded",
                request_id=request.request_id,
                session_id=request.session_id,
            )
            raise
        except _ApprovalPausedError as paused:
            # A pause is not an error: retry/fallback policies must not apply.
            approval = paused.approval
            pending_names = [str(tr.get("name", "")) for tr in approval.tool_requests]
            self._audit(
                "approval.requested",
                agent=definition.metadata.name,
                tools=pending_names,
                continuation_id=approval.continuation_id,
            )
            self._telemetry.logger.warning(
                "invocation paused awaiting approval",
                request_id=request.request_id,
                tools=pending_names,
            )
            return AgentResponse(
                output={"content": "", "tool_results": approval.all_tool_results},
                request_id=approval.request_id or request.request_id,
                session_id=approval.session_id,
                status="approval_required",
                metadata={
                    "continuation_id": approval.continuation_id,
                    "pending_tools": pending_names,
                },
            )
        except Exception as exc:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            self._telemetry.logger.error(
                "invocation failed",
                request_id=request.request_id,
                session_id=request.session_id,
                error=str(exc),
            )
            if semantics.error_policy is ErrorPolicy.RETRY:
                if deadline.expired:
                    raise
                try:
                    response = await run_once()
                except Exception as retry_exc:
                    self._telemetry.increment("agent_invocation_errors_total", labels)
                    raise retry_exc from exc
            elif semantics.error_policy is ErrorPolicy.FALLBACK:
                response = AgentResponse(
                    output={},
                    request_id=request.request_id,
                    session_id=request.session_id,
                    status="error",
                    error=str(exc),
                    metadata={"error_policy": "fallback"},
                )
            else:
                raise

        latency_ms = round((time.monotonic() - start_time) * 1000, 2)
        self._telemetry.increment("agent_invocations_total", labels)
        self._telemetry.record("agent_invocation_latency_ms", latency_ms, labels)
        return response

    async def _invoke_inner(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        trace_id: str,
        deadline: _InvocationDeadline,
    ) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        model_provider: ModelProvider = agent._internal["model_provider"]
        tools: dict[str, Tool] = agent._internal["tools"]
        skills = agent._internal["skills"]
        session_provider: SessionProvider | None = agent._internal["session_provider"]
        session_ttl: int | None = agent._internal["session_ttl"]
        memory_provider: MemoryProvider | None = agent._internal["memory_provider"]
        memory_scope: str = agent._internal["memory_scope"]

        semantics = definition.spec.runtime
        max_iterations = semantics.max_iterations or self._config.default_max_iterations
        labels = {"agent": definition.metadata.name}

        # Approval continuation: restore the paused conversation state before
        # anything else. Unknown, expired, or foreign continuations fail fast.
        resume: PendingApproval | None = None
        approved: bool = False
        if request.continuation_id:
            resume = await self._approval_store.get(request.continuation_id)
            if resume is None or resume.agent_id != agent.identity.agent_id:
                raise ContinuationNotFoundError("unknown or expired continuation")
            await self._approval_store.delete(request.continuation_id)
            approved = request.approval_decision == "approve"

        session_id = request.session_id or (resume.session_id if resume else None)
        input_payload: dict[str, Any] = (
            resume.input_payload if resume is not None else request.input
        )

        bound_identity = get_invocation_identity()
        tenant_id = (
            bound_identity.user.tenant_id
            if bound_identity is not None and bound_identity.user is not None
            else None
        )
        agent_span = self._telemetry.start_span(
            "agent.invoke",
            trace_id=trace_id,
            attributes={
                "agent": definition.metadata.name,
                "session_id": session_id,
                "caller_id": (
                    bound_identity.caller.caller_id
                    if bound_identity is not None and bound_identity.caller is not None
                    else None
                ),
            },
        )

        session = None
        # Keep the tail length so completed invocations can persist the full
        # turn (including assistant tool calls and tool results) without
        # duplicating the prior session history.  The system prompt is never
        # persisted.
        history_tail_length = 0
        if session_provider is not None and session_id:
            session = await deadline.run(
                _call_with_supported_kwargs(session_provider.get, session_id, tenant_id=tenant_id)
            )
            if session is None:
                session = await deadline.run(
                    _call_with_supported_kwargs(
                        session_provider.create,
                        session_id,
                        ttl_seconds=session_ttl,
                        tenant_id=tenant_id,
                    )
                )

        inner_start = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(definition, skills)}
        ]
        if session is not None:
            history_tail = session.messages[-_MAX_SESSION_HISTORY_MESSAGES:]
            history_tail_length = len(history_tail)
            messages.extend(history_tail)
        messages.append({"role": "user", "content": json.dumps(input_payload, default=str)})

        model_ref = definition.spec.dependencies.model
        model_config = ModelConfig(
            ref=model_ref.ref if model_ref else "default",
            provider=model_ref.provider if model_ref else None,
            model_id=model_ref.model_id if model_ref else None,
            endpoint=model_ref.endpoint if model_ref else None,
            credential_ref=model_ref.credential_ref if model_ref else None,
            generation=model_ref.generation if model_ref else {},
            timeout_seconds=model_ref.timeout_seconds if model_ref else None,
        )
        tool_schemas = [
            {
                "name": tool.metadata.name,
                "description": tool.metadata.description,
                "input_schema": tool.input_schema.parameters,
            }
            for tool in tools.values()
        ]

        all_tool_results: list[dict[str, Any]] = []
        usage: dict[str, int] = {}
        iterations = 0
        max_iterations_reached = False

        if resume is not None:
            # Replay the paused tool wave: approved requests execute (hard
            # policy denials still apply), denied requests feed the model a
            # denial so it can respond. The conversation continues from the
            # paused snapshot, which already ends with the assistant
            # tool-request turn.
            iterations = resume.iterations
            all_tool_results = list(resume.all_tool_results)
            if approved:
                self._audit(
                    "approval.granted",
                    agent=definition.metadata.name,
                    tools=[str(tr.get("name", "")) for tr in resume.tool_requests],
                    continuation_id=resume.continuation_id,
                )
                tool_results = await self._execute_tools(
                    tools,
                    resume.tool_requests,
                    trace_id,
                    agent_span.span_id,
                    labels,
                    deadline=deadline,
                    skip_side_effect_approval=True,
                )
            else:
                self._audit(
                    "approval.denied",
                    agent=definition.metadata.name,
                    tools=[str(tr.get("name", "")) for tr in resume.tool_requests],
                    continuation_id=resume.continuation_id,
                )
                tool_results = [
                    {
                        "tool": str(tool_request.get("name", "")),
                        "output": None,
                        "error": "denied by caller: approval denied",
                        "latency_ms": 0.0,
                        "denied": True,
                    }
                    for tool_request in resume.tool_requests
                ]
            all_tool_results.extend(tool_results)
            # Continue from the paused transcript verbatim; freshly built
            # history is discarded so the assistant tool-request turn stays
            # in place ahead of the replayed results.
            messages = list(resume.messages)
            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "name": result["tool"],
                        "content": json.dumps(result["error"] or result["output"], default=str),
                    }
                )

        while True:
            iterations += 1
            model_span = self._telemetry.start_span(
                "model.generate",
                trace_id=trace_id,
                parent_span_id=agent_span.span_id,
                attributes={"iteration": iterations},
            )
            model_start = time.monotonic()
            try:
                model_call = model_provider.generate(
                    model_config, messages, tools=tool_schemas or None
                )
                response = await deadline.run(model_call, cap=model_config.timeout_seconds)
            except Exception as exc:
                model_span.add_event("model.error", {"error": str(exc)})
                self._telemetry.finish_span(model_span)
                raise
            model_latency = round((time.monotonic() - model_start) * 1000, 2)
            model_span.set_attribute("latency_ms", model_latency)
            model_span.add_event("model.response", {"finish_reason": response.finish_reason})
            self._telemetry.finish_span(model_span)

            self._telemetry.record("model_latency_ms", model_latency, labels)
            usage = {k: usage.get(k, 0) + v for k, v in response.usage.items()}
            self._telemetry.record_model_usage(response.usage, labels)

            assistant_message: dict[str, Any] = {"role": "assistant", "content": response.content}
            if response.tool_requests:
                # The assistant tool_calls payload must stay in the history —
                # providers require it to pair the following tool results.
                assistant_message["tool_calls"] = [
                    {
                        "id": str(request.get("id") or _new_call_id()),
                        "type": "function",
                        "function": {
                            "name": str(request.get("name", "")),
                            "arguments": json.dumps(request.get("arguments") or {}, default=str),
                        },
                    }
                    for request in response.tool_requests
                ]
                for call, tool_request in zip(
                    assistant_message["tool_calls"], response.tool_requests, strict=True
                ):
                    tool_request["id"] = call["id"]
            messages.append(assistant_message)

            if not response.tool_requests:
                break
            if iterations >= max_iterations:
                max_iterations_reached = True
                self._telemetry.logger.warning(
                    "max_iterations reached with pending tool requests",
                    request_id=request.request_id,
                    max_iterations=max_iterations,
                )
                break

            if self._approval_needed(agent, tools, response.tool_requests):
                continuation_id = request.continuation_id or str(uuid4())
                approval = PendingApproval(
                    continuation_id=continuation_id,
                    agent_id=agent.identity.agent_id,
                    tool_requests=[dict(tr) for tr in response.tool_requests],
                    messages=[dict(m) for m in messages],
                    all_tool_results=list(all_tool_results),
                    iterations=iterations,
                    request_id=request.request_id,
                    session_id=session_id,
                    input_payload=dict(input_payload),
                )
                await self._approval_store.save(approval)
                raise _ApprovalPausedError(approval)

            tool_results = await self._execute_tools(
                tools,
                response.tool_requests,
                trace_id,
                agent_span.span_id,
                labels,
                deadline=deadline,
            )
            all_tool_results.extend(tool_results)
            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.get("tool_call_id"),
                        "name": result["tool"],
                        "content": json.dumps(result["error"] or result["output"], default=str),
                    }
                )

        if session is not None:
            # Persist the complete current turn so the next invocation can
            # replay provider-required assistant tool calls alongside their
            # matching tool results.  Only messages after the prior history
            # tail are new; the system prompt remains runtime-only.
            turn_start = 1 + history_tail_length
            session.messages.extend(dict(message) for message in messages[turn_start:])
            await deadline.run(
                session_provider.update(session, ttl_seconds=session_ttl)  # type: ignore[union-attr]
            )

        if (
            memory_provider is not None
            and (self._config.memory_policy or MemoryPolicy()).auto_store
        ):
            await deadline.run(
                _call_with_supported_kwargs(
                    memory_provider.store,
                    MemoryEntry(
                        key=f"invocation:{request.request_id or uuid4()}",
                        value={
                            "input": request.input,
                            "output": response.content,
                        },
                        scope=memory_scope,
                        tenant_id=tenant_id,
                    ),
                )
            )

        agent_span.set_attribute("iterations", iterations)
        agent_span.add_event(
            "agent.response",
            {"finish_reason": response.finish_reason, "tools_called": len(all_tool_results)},
        )
        self._telemetry.finish_span(agent_span)

        return AgentResponse(
            output={"content": response.content, "tool_results": all_tool_results},
            request_id=request.request_id,
            session_id=session_id,
            status="success",
            metadata={
                "usage": usage,
                "latency_ms": round((time.monotonic() - inner_start) * 1000, 2),
                "model_ref": model_config.ref,
                "tools_called": [r["tool"] for r in all_tool_results if not r.get("denied")],
                "iterations": iterations,
                "max_iterations_reached": max_iterations_reached,
                "unresolved_tools": agent._internal["unresolved_tools"],
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _approval_needed(
        self,
        agent: RuntimeAgent,
        tools: dict[str, Tool],
        tool_requests: list[dict[str, Any]],
    ) -> bool:
        """Whether any pending tool request is waiting on an approval policy."""
        if self._policy_evaluator is None:
            return False
        for tool_request in tool_requests:
            tool = tools.get(tool_request.get("name", ""))
            if tool is None:
                continue
            if not self._policy_evaluator.evaluate_tool(tool.metadata.name).allowed:
                continue
            decision = self._policy_evaluator.evaluate_side_effect(tool.metadata.name)
            if decision.requires_approval:
                return True
        return False

    def _deny(
        self,
        labels: dict[str, str],
        tool_name: str,
        reason: str,
        event: str = "policy.tool_denied",
    ) -> None:
        self._telemetry.increment("policy_denials_total", {**labels, "tool": tool_name})
        self._telemetry.logger.warning("tool denied by policy", tool=tool_name, reason=reason)
        self._audit(event, agent=labels.get("agent"), tool=tool_name, reason=reason)

    def _audit(self, event: str, **fields: Any) -> None:
        if self._config.audit is not None:
            self._config.audit.record(event, **fields)

    def _resolve_tools(self, definition: MicroAgentDefinition) -> tuple[dict[str, Tool], list[str]]:
        """Resolve definition-declared tools against the configured/built-in registry."""
        registry = builtin_tool_registry()
        registry.update(self._config.tool_registry or {})
        tools: dict[str, Tool] = {}
        unresolved: list[str] = []
        for tool_def in definition.spec.dependencies.tools:
            if tool_def.name in tools:
                continue
            tool = registry.get(tool_def.name)
            if tool is None:
                unresolved.append(tool_def.name)
                continue
            tools[tool_def.name] = tool
        return tools, unresolved

    def _system_prompt(self, definition: MicroAgentDefinition, skills: list[Any]) -> str:
        """Build the system prompt; skills are semantic capabilities, not tools."""
        parts = [definition.spec.behavior.instructions]
        if skills:
            lines = [
                f"- {s.id} ({s.name}): {s.description or ''}"
                f"{' [' + ', '.join(s.tags) + ']' if s.tags else ''}"
                for s in skills
            ]
            parts.append(
                "Declared capabilities (skills) — what this agent can do for "
                "callers:\n" + "\n".join(lines)
            )
        return "\n\n".join(parts)

    async def _execute_tools(
        self,
        tools: dict[str, Tool],
        tool_requests: list[dict[str, Any]],
        trace_id: str,
        parent_span_id: str,
        labels: dict[str, str],
        deadline: _InvocationDeadline | None = None,
        skip_side_effect_approval: bool = False,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for tool_request in tool_requests:
            tool_name = tool_request.get("name", "")
            tool_call_id = tool_request.get("id")
            tool = tools.get(tool_name)
            if tool is None:
                results.append(
                    {
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "output": None,
                        "error": f"unknown tool: {tool_name}",
                        "latency_ms": 0.0,
                    }
                )
                continue

            arguments = tool_request.get("arguments", {})
            validation_error = _validate_tool_arguments(tool, arguments)
            if validation_error is not None:
                results.append(
                    {
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "output": None,
                        "error": f"invalid tool arguments: {validation_error}",
                        "latency_ms": 0.0,
                    }
                )
                continue

            # Deterministic platform policy — evaluated outside the prompt so
            # prompt injection cannot override it.
            if self._policy_evaluator is not None:
                tool_decision = self._policy_evaluator.evaluate_tool(tool_name)
                if not tool_decision.allowed:
                    self._deny(labels, tool_name, tool_decision.reason)
                    results.append(
                        {
                            "tool": tool_name,
                            "tool_call_id": tool_call_id,
                            "output": None,
                            "error": f"denied by policy: {tool_decision.reason}",
                            "latency_ms": 0.0,
                            "denied": True,
                        }
                    )
                    continue
                side_effect_decision = self._policy_evaluator.evaluate_side_effect(tool_name)
                approval_satisfied = (
                    skip_side_effect_approval and side_effect_decision.requires_approval
                )
                if not side_effect_decision.allowed and not approval_satisfied:
                    self._deny(
                        labels,
                        tool_name,
                        side_effect_decision.reason,
                        event="policy.side_effect_denied",
                    )
                    results.append(
                        {
                            "tool": tool_name,
                            "tool_call_id": tool_call_id,
                            "output": None,
                            "error": (
                                f"denied by side-effect policy: {side_effect_decision.reason}"
                            ),
                            "latency_ms": 0.0,
                            "denied": True,
                        }
                    )
                    continue

            # Idempotency/deduplication for side-effect operations.
            registry = self._config.operation_registry
            operation = None
            if registry is not None:
                bound_identity = get_invocation_identity()
                tenant_id = (
                    bound_identity.user.tenant_id
                    if bound_identity is not None and bound_identity.user is not None
                    else None
                )
                operation = _new_operation(tool_name, arguments, tenant_id=tenant_id)
                claim = getattr(registry, "claim", None)
                if callable(claim):
                    claimed, prior = await _maybe_await(claim(operation))
                else:
                    duplicate = await _maybe_await(registry.is_duplicate(operation))
                    if duplicate:
                        find_result = registry.find_by_idempotency_key
                        if operation.tenant_id is None:
                            prior = await _maybe_await(find_result(operation.idempotency_key or ""))
                        else:
                            prior = await _maybe_await(
                                find_result(operation.idempotency_key or "", operation.tenant_id)
                            )
                    else:
                        prior = None
                    claimed = not duplicate
                if not claimed:
                    results.append(
                        {
                            "tool": tool_name,
                            "tool_call_id": tool_call_id,
                            "output": prior.output if prior else None,
                            "error": (
                                "operation is already in progress"
                                if prior is not None and prior.status == "in_progress"
                                else (prior.error if prior else None)
                            ),
                            "latency_ms": 0.0,
                            "was_deduplicated": True,
                        }
                    )
                    continue

            span = self._telemetry.start_span(
                f"tool.{tool_name}",
                trace_id=trace_id,
                parent_span_id=parent_span_id,
            )
            tool_start = time.monotonic()
            try:
                timeout = tool.metadata.timeout_seconds or _DEFAULT_TOOL_TIMEOUT_SECONDS
                if deadline is None:
                    result = await asyncio.wait_for(tool.execute(arguments), timeout)
                else:
                    result = await deadline.run(tool.execute(arguments), cap=timeout)
                output: Any = result.output
                error: str | None = result.error
                if result.is_error and not error:
                    error = "tool reported an error"
            except TimeoutError:
                if deadline is not None and deadline.expired:
                    self._telemetry.finish_span(span)
                    raise
                output = None
                error = f"tool '{tool_name}' timed out"
            except Exception as exc:  # noqa: BLE001 — tool failures become results
                output = None
                error = str(exc)

            if registry is not None and operation is not None:
                await _maybe_await(
                    registry.record(
                        operation,
                        OperationResult(
                            operation_id=operation.operation_id,
                            status="failed" if error else "success",
                            output=output,
                            error=error,
                        ),
                    )
                )

            tool_latency = round((time.monotonic() - tool_start) * 1000, 2)
            span.set_attribute("latency_ms", tool_latency)
            if error:
                span.add_event("tool.error", {"error": error})
            self._telemetry.finish_span(span)

            self._telemetry.increment("tool_calls_total", {**labels, "tool": tool_name})
            self._telemetry.record("tool_latency_ms", tool_latency, {**labels, "tool": tool_name})
            results.append(
                {
                    "tool": tool_name,
                    "tool_call_id": tool_call_id,
                    "output": output,
                    "error": error,
                    "latency_ms": tool_latency,
                }
            )
        return results


_ARGUMENT_TYPE_CHECKS = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def _new_call_id() -> str:
    """A provider-shaped call id for requests that arrive without one."""
    return f"call_{uuid4().hex[:24]}"


def _validate_tool_arguments(tool: Tool, arguments: Any) -> str | None:
    """Validate a tool request against the declared JSON Schema contract.

    Checks the argument envelope, required properties, and basic property
    types; detailed schema validation stays with the tool implementation.
    """
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    schema = tool.input_schema.parameters or {}
    for name in schema.get("required") or []:
        if name not in arguments:
            return f"missing required argument '{name}'"
    properties = schema.get("properties") or {}
    for name, value in arguments.items():
        expected = (properties.get(name) or {}).get("type")
        check = _ARGUMENT_TYPE_CHECKS.get(str(expected))
        if check is not None and not check(value):
            return f"argument '{name}' must be of type {expected}"
    return None


def _new_operation(
    tool_name: str, arguments: dict[str, Any], *, tenant_id: str | None = None
) -> Operation:
    """Build an Operation for a tool call; arguments may carry an idempotency key."""
    key = arguments.get("idempotency_key")
    return Operation(
        name=tool_name,
        arguments=arguments,
        idempotency_key=str(key) if key else None,
        tenant_id=tenant_id,
    )
