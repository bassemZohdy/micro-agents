"""Framework-neutral governance base for Micro-Agent runtimes.

Everything here is independent of any agent framework: configuration, lifecycle,
dependency probes, retry/fallback/circuit-breaker handling, deadlines, tool
resolution and governed tool execution (policy, argument validation, idempotent
operations, approvals, audit and telemetry). A runtime subclasses
:class:`GovernedRuntime` and supplies only orchestration by implementing
``_invoke_inner``; the bounded model/tool loop in ``runtimes/adk`` and the
LangGraph ``StateGraph`` in ``runtimes/langgraph`` are two such orchestrations.
"""

from __future__ import annotations

import asyncio
import json
from abc import abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
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
from micro_agent.governance.checkpoints import ensure_neutral_checkpoint, neutral_runtime_state
from micro_agent.governance.invocation import InvocationGovernor
from micro_agent.governance.models import GovernedModelProvider, ModelCallScope, use_model_scope
from micro_agent.governance.support import (
    InvocationDeadline,
    maybe_await,
)
from micro_agent.governance.tools import ToolCallScope, ToolGovernor
from micro_agent.health import DependencyProbe, HealthStatus
from micro_agent.knowledge import (
    KnowledgeRetriever,
    KnowledgeSource,
)
from micro_agent.mcp import McpConnectionManager
from micro_agent.memory import MemoryPolicy, MemoryProvider
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelProvider,
)
from micro_agent.observability import AuditSink, Telemetry
from micro_agent.resilience import CircuitBreaker
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.security import (
    AgentPolicy,
    ApprovalStore,
    InMemoryApprovalStore,
    InvocationIdentity,
    OperationRegistryProtocol,
    PendingApproval,
    PolicyEvaluator,
    build_security_context,
    reset_invocation_identity,
    resolve_workload_identity,
    set_invocation_identity,
)
from micro_agent.session import SessionProvider
from micro_agent.tools import Tool, builtin_tool_registry, normalize_tool_side_effect
from micro_agent.tools.plugin import load_plugin_tools


@dataclass
class ApprovalReplay:
    """The outcome of resuming a paused tool wave after a decision."""

    pending: PendingApproval
    approved: bool
    #: Results of the paused wave (executed when approved, denials otherwise).
    tool_results: list[dict[str, Any]]
    #: The paused transcript followed by the wave's tool messages.
    messages: list[dict[str, Any]]


@dataclass
class GovernedRuntimeConfig:
    """Configuration for the ADK runtime."""

    fake_model_config: FakeModelConfig = field(default_factory=FakeModelConfig)
    model_provider: ModelProvider | None = None
    session_provider: SessionProvider | None = None
    checkpoint_store: CheckpointStore | None = None
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


class GovernedRuntime(AgentRuntime):
    """Runtime base that owns governance; subclasses own orchestration.

    Provides RuntimeSemantics enforcement (overall timeout, retry, fallback and
    circuit breaking), session and memory integration, governed tool execution
    and telemetry (spans, metrics, structured logs). No framework types appear
    here or on the public interface.
    """

    def __init__(self, config: GovernedRuntimeConfig | None = None) -> None:
        self._config = config or GovernedRuntimeConfig()
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
        self._tool_governor = ToolGovernor(
            telemetry=self._telemetry,
            policy_evaluator=self._policy_evaluator,
            operation_registry=self._config.operation_registry,
            audit=self._audit,
        )
        self._invocations = InvocationGovernor(telemetry=lambda: self._telemetry, audit=self._audit)
        self._knowledge_refs: list[KnowledgeSource] = []
        self._started = False

    # ------------------------------------------------------------------
    # Capabilities and lifecycle
    # ------------------------------------------------------------------

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            streaming=self._model_provider.capabilities().streaming,
            memory=self._config.memory_provider is not None,
            mcp=self._config.mcp_manager is not None,
            a2a=False,
            structured_output=self._model_provider.capabilities().structured_output,
            checkpointing=self._config.checkpoint_store is not None,
        )

    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        identity = AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        )
        capabilities = AgentCapabilities(memory=self._config.memory_provider is not None)

        tools, unresolved = self._resolve_tools(definition)
        tool_side_effects = {
            tool_definition.name: tool_definition.side_effect
            for tool_definition in definition.spec.dependencies.tools
        }
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
            KnowledgeSource(
                ref=ref.ref,
                source_type=ref.source_type,
                version=ref.version,
                max_results=ref.max_results,
                max_context_characters=ref.max_context_characters,
            )
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
                "model_provider": GovernedModelProvider(
                    self._model_provider, lambda: self._telemetry
                ),
                "tools": tools,
                "tool_side_effects": tool_side_effects,
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

        checkpoint_store = self._config.checkpoint_store
        if checkpoint_store is not None:
            try:
                healthy = await checkpoint_store.health_check()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("checkpoint store failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("checkpoint store failed its health check at startup")

        memory_provider = self._config.memory_provider
        if memory_provider is not None:
            try:
                await memory_provider.list_entries()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("memory provider failed its health check at startup") from exc

        operation_registry = self._config.operation_registry
        if operation_registry is not None:
            try:
                healthy = await maybe_await(operation_registry.health_check())
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("operation registry failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("operation registry failed its health check at startup")

        approval_store = self._config.approval_store
        if approval_store is not None:
            try:
                healthy = await approval_store.health_check()
            except Exception as exc:  # noqa: BLE001 — normalize startup failures
                raise RuntimeError("approval store failed its health check at startup") from exc
            if not healthy:
                raise RuntimeError("approval store failed its health check at startup")

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
            await maybe_await(aclose())
        for provider in (
            self._config.session_provider,
            self._config.memory_provider,
            self._config.operation_registry,
            self._config.approval_store,
        ):
            provider_close = getattr(provider, "aclose", None)
            if provider_close is not None:
                await maybe_await(provider_close())
        if self._config.knowledge_provider is not None:
            knowledge_close = getattr(self._config.knowledge_provider, "aclose", None)
            if knowledge_close is not None:
                await maybe_await(knowledge_close())
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
            return await maybe_await(operation_registry.health_check())

        if operation_registry is not None:
            probes["operation_registry"] = _operation_probe
        approval_store = self._config.approval_store
        if approval_store is not None:
            probes["approval_store"] = approval_store.health_check
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

    async def stream(
        self, agent: RuntimeAgent, request: AgentRequest
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream provider deltas and finish with the normal AgentResponse contract."""
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
                response = await self._invoke(agent, request, emit_delta=emit)
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
        emit_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        semantics = definition.spec.runtime
        trace_id = request.request_id or str(uuid4())
        deadline = InvocationDeadline.from_seconds(
            semantics.timeout_seconds,
            request.timeout_seconds,
        )
        side_effect_state: dict[str, Any] = {"attempted_tools": []}
        emitted_stream_output = False

        async def tracked_emit(delta: str) -> None:
            nonlocal emitted_stream_output
            if not delta or emit_delta is None:
                return
            emitted_stream_output = True
            await emit_delta(delta)

        async def run_once() -> AgentResponse:
            # Fresh per attempt: usage and span numbering restart on a retry. The
            # orchestration sets ``parent_span_id`` once it has opened its span.
            scope = ModelCallScope(
                trace_id=trace_id,
                labels={"agent": definition.metadata.name},
                deadline=deadline,
                emit_delta=tracked_emit if emit_delta is not None else None,
            )
            with use_model_scope(scope):
                return await self._invoke_inner(
                    agent,
                    request,
                    trace_id,
                    deadline,
                    side_effect_state=side_effect_state,
                    emit_delta=tracked_emit if emit_delta is not None else None,
                )

        return await self._invocations.run(
            definition,
            request,
            attempt=run_once,
            deadline=deadline,
            side_effect_state=side_effect_state,
            streamed_output=lambda: emitted_stream_output,
        )

    @abstractmethod
    async def _invoke_inner(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        trace_id: str,
        deadline: InvocationDeadline,
        *,
        side_effect_state: dict[str, Any] | None = None,
        emit_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentResponse:
        """Run one orchestration attempt; retry and fallback wrap it."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Checkpoint and resume (shared by every orchestration)
    # ------------------------------------------------------------------

    async def _load_checkpoint(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        deadline: InvocationDeadline,
        tenant_id: str | None,
    ) -> CheckpointRecord | None:
        """Load the checkpoint a request resumes from, or ``None`` for a fresh run."""
        if request.checkpoint_id is None:
            return None
        store = self._config.checkpoint_store
        if store is None:
            raise CheckpointNotFoundError("checkpointing is not configured for this runtime")
        checkpoint = await deadline.run(store.get(request.checkpoint_id, tenant_id=tenant_id))
        if checkpoint is None or checkpoint.agent_id != agent.identity.agent_id:
            raise CheckpointNotFoundError("unknown, expired, or foreign checkpoint")
        ensure_neutral_checkpoint(checkpoint)
        if request.input:
            raise ValueError("checkpoint resume must not include new input")
        return checkpoint

    async def _save_checkpoint(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        deadline: InvocationDeadline,
        tenant_id: str | None,
        *,
        checkpoint_id: str,
        session_id: str | None,
        input_payload: dict[str, Any],
        messages: list[dict[str, Any]],
        all_tool_results: list[dict[str, Any]],
        iterations: int,
        usage: dict[str, int],
        history_tail_length: int,
    ) -> None:
        """Snapshot replay-safe state at a tool-round boundary (no-op without a store)."""
        store = self._config.checkpoint_store
        if store is None:
            return
        await deadline.run(
            store.save(
                CheckpointRecord(
                    checkpoint_id=checkpoint_id,
                    agent_id=agent.identity.agent_id,
                    request_id=request.request_id,
                    session_id=session_id,
                    input_payload=dict(input_payload),
                    messages=[dict(message) for message in messages],
                    all_tool_results=list(all_tool_results),
                    iterations=iterations,
                    usage=dict(usage),
                    history_tail_length=history_tail_length,
                    tenant_id=tenant_id,
                    runtime_state=neutral_runtime_state(),
                )
            )
        )

    async def _delete_checkpoint(
        self, checkpoint_id: str, deadline: InvocationDeadline, tenant_id: str | None
    ) -> None:
        """Drop a checkpoint (no-op without a store)."""
        store = self._config.checkpoint_store
        if store is not None:
            await deadline.run(store.delete(checkpoint_id, tenant_id=tenant_id))

    def _tool_wave_is_replay_safe(
        self,
        agent: RuntimeAgent,
        tools: dict[str, Tool],
        tool_requests: list[dict[str, Any]],
    ) -> bool:
        """Return true only when replay cannot repeat a declared side effect."""
        side_effects = agent._internal.get("tool_side_effects") or {}
        for tool_request in tool_requests:
            tool_name = str(tool_request.get("name", ""))
            tool = tools.get(tool_name)
            if tool is None:
                continue
            classification = normalize_tool_side_effect(
                side_effects.get(tool_name) or tool.metadata.side_effect
            )
            if classification != "read_only":
                return False
        return True

    # ------------------------------------------------------------------
    # Approval pause and resume (shared by every orchestration)
    # ------------------------------------------------------------------

    def _approval_needed(
        self,
        agent: RuntimeAgent,
        tools: dict[str, Tool],
        tool_requests: list[dict[str, Any]],
    ) -> bool:
        """Whether any pending tool request is waiting on an approval policy."""
        return self._tool_governor.approval_needed(
            tools, tool_requests, agent._internal.get("tool_side_effects")
        )

    async def _load_pending_approval(
        self, agent: RuntimeAgent, request: AgentRequest
    ) -> tuple[PendingApproval, bool]:
        """Consume a continuation: unknown, expired or foreign ones fail fast."""
        assert request.continuation_id
        pending = await self._approval_store.get(request.continuation_id)
        if pending is None or pending.agent_id != agent.identity.agent_id:
            raise ContinuationNotFoundError("unknown or expired continuation")
        await self._approval_store.delete(request.continuation_id)
        return pending, request.approval_decision == "approve"

    async def _save_pending_approval(
        self,
        agent: RuntimeAgent,
        request: AgentRequest,
        *,
        tool_requests: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        all_tool_results: list[dict[str, Any]],
        iterations: int,
        session_id: str | None,
        input_payload: dict[str, Any],
    ) -> PendingApproval:
        """Persist a paused tool wave so a later decision can resume it."""
        approval = PendingApproval(
            continuation_id=request.continuation_id or str(uuid4()),
            agent_id=agent.identity.agent_id,
            tool_requests=[dict(tr) for tr in tool_requests],
            messages=[dict(m) for m in messages],
            all_tool_results=list(all_tool_results),
            iterations=iterations,
            request_id=request.request_id,
            session_id=session_id,
            input_payload=dict(input_payload),
        )
        await self._approval_store.save(approval)
        return approval

    async def _replay_pending_approval(
        self,
        agent: RuntimeAgent,
        tools: dict[str, Tool],
        pending: PendingApproval,
        approved: bool,
        *,
        trace_id: str,
        parent_span_id: str,
        labels: dict[str, str],
        deadline: InvocationDeadline,
        side_effect_state: dict[str, Any] | None,
    ) -> ApprovalReplay:
        """Replay a paused wave: approved calls run (hard denials still apply),
        denied calls feed the model a denial so it can answer."""
        definition: MicroAgentDefinition = agent._internal["definition"]
        tool_names = [str(tr.get("name", "")) for tr in pending.tool_requests]
        if approved:
            self._audit(
                "approval.granted",
                agent=definition.metadata.name,
                tools=tool_names,
                continuation_id=pending.continuation_id,
            )
            tool_results = await self._execute_tools(
                tools,
                pending.tool_requests,
                trace_id,
                parent_span_id,
                labels,
                deadline=deadline,
                skip_side_effect_approval=True,
                side_effects=agent._internal.get("tool_side_effects"),
                side_effect_state=side_effect_state,
            )
        else:
            self._audit(
                "approval.denied",
                agent=definition.metadata.name,
                tools=tool_names,
                continuation_id=pending.continuation_id,
            )
            tool_results = [
                {
                    "tool": str(tool_request.get("name", "")),
                    "tool_call_id": tool_request.get("id"),
                    "output": None,
                    "error": "denied by caller: approval denied",
                    "latency_ms": 0.0,
                    "denied": True,
                }
                for tool_request in pending.tool_requests
            ]
        messages = [dict(message) for message in pending.messages]
        for result in tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.get("tool_call_id"),
                    "name": result["tool"],
                    "content": json.dumps(result["error"] or result["output"], default=str),
                }
            )
        return ApprovalReplay(pending, approved, tool_results, messages)

    def _deny(
        self,
        labels: dict[str, str],
        tool_name: str,
        reason: str,
        event: str = "policy.tool_denied",
    ) -> None:
        self._tool_governor.deny(labels, tool_name, reason, event)

    def _audit(self, event: str, **fields: Any) -> None:
        if self._config.audit is not None:
            self._config.audit.record(event, **fields)

    @property
    def _circuits(self) -> dict[str, CircuitBreaker]:
        """Per-agent circuit breakers (owned by the invocation governor)."""
        return self._invocations.circuits

    def _resolve_tools(self, definition: MicroAgentDefinition) -> tuple[dict[str, Tool], list[str]]:
        """Resolve definition-declared tools against the configured/built-in registry."""
        registry = builtin_tool_registry()
        registry.update(load_plugin_tools())
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
        deadline: InvocationDeadline | None = None,
        skip_side_effect_approval: bool = False,
        side_effects: dict[str, str] | None = None,
        side_effect_state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one tool wave through the shared :class:`ToolGovernor`."""
        scope = ToolCallScope(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            labels=labels,
            deadline=deadline,
            skip_side_effect_approval=skip_side_effect_approval,
            side_effects=side_effects,
            side_effect_state=side_effect_state,
        )
        return [
            await self._tool_governor.run_request(tools, tool_request, scope)
            for tool_request in tool_requests
        ]


def new_call_id() -> str:
    """A provider-shaped call id for requests that arrive without one."""
    return f"call_{uuid4().hex[:24]}"
