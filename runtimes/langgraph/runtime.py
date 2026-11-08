"""LangGraph runtime adapter.

The control flow of an invocation is a genuine LangGraph ``StateGraph``::

    prepare -> model -> (tools -> model)* -> finalize

``prepare`` loads the session and knowledge context and builds the prompt,
``model`` calls the configured :class:`~micro_agent.models.ModelProvider`,
``tools`` executes requested tools, and ``finalize`` persists the turn. The
loop edge is bounded by the definition's ``max_iterations``.

Governance is shared through the framework-neutral
:class:`micro_agent.governance.GovernedRuntime` rather than re-implemented:
deterministic policy, argument validation, idempotent side-effect operations,
audit, telemetry, retry/fallback semantics, deadlines and circuit breaking all
come from that base. Only the orchestration is
LangGraph's. No LangGraph or LangChain type crosses the public
:class:`~micro_agent.runtime.AgentRuntime` boundary.

Capability reporting is honest about what this adapter does not do:

* ``checkpointing`` is false and ``checkpoint_id`` requests are rejected.
* Approval continuations use the shared ``ApprovalStore``: a tool whose
  side-effect policy requires approval pauses the invocation
  (``approval_required``) and a later request with the ``continuation_id`` and
  decision resumes it, exactly as in the reference runtime.
* Models are reached through the Micro-Agent provider SPI, not LangChain chat
  models, so every built-in provider works unchanged.

LangGraph is an optional pinned extra (``pip install micro-agents[langgraph]``)
and is imported lazily so the base install and fake-provider CI never need it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import uuid4

from micro_agent.core import AgentCapabilities, AgentRequest, AgentResponse
from micro_agent.definition import MicroAgentDefinition
from micro_agent.governance import (
    MAX_SESSION_HISTORY_MESSAGES,
    ApprovalPausedError,
    GovernedRuntime,
    GovernedRuntimeConfig,
    InvocationDeadline,
    call_with_supported_kwargs,
    new_call_id,
)
from micro_agent.knowledge import build_knowledge_query, retrieve_knowledge_context
from micro_agent.memory import MemoryEntry, MemoryPolicy
from micro_agent.models import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    structured_output_generation,
)
from micro_agent.runtime import RuntimeAgent, RuntimeCapabilities
from micro_agent.security import PendingApproval, get_invocation_identity
from micro_agent.session import SessionProvider
from micro_agent.tools import Tool

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
else:
    # LangGraph resolves node signatures at runtime, so the name must exist.
    try:
        from langchain_core.runnables import RunnableConfig
    except ImportError:  # pragma: no cover - the optional extra is not installed
        RunnableConfig = dict

# One model step plus one tool step per iteration, plus prepare/finalize slack.
_STEPS_PER_ITERATION = 2
_RECURSION_SLACK = 6


class LangGraphError(RuntimeError):
    """Raised when the LangGraph adapter cannot run (e.g. missing extra)."""


@dataclass
class LangGraphRuntimeConfig(GovernedRuntimeConfig):
    """Configuration for the LangGraph runtime.

    It accepts the same service wiring as the reference runtime, including the
    durable ``ApprovalStore``. The checkpoint store is unsupported here and
    rejected at construction rather than silently ignored.
    """


class _State(TypedDict, total=False):
    """Loop state carried between graph nodes (plain, serializable values)."""

    messages: list[dict[str, Any]]
    tool_requests: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    usage: dict[str, int]
    iterations: int
    content: str
    finish_reason: str
    max_iterations_reached: bool


@dataclass
class _Run:
    """Per-invocation context passed to nodes through ``configurable``.

    It holds live objects (session, deadline, spans) that must not become part
    of the graph state.
    """

    agent: RuntimeAgent
    request: AgentRequest
    trace_id: str
    deadline: InvocationDeadline
    side_effect_state: dict[str, Any] | None
    emit_delta: Callable[[str], Awaitable[None]] | None
    definition: MicroAgentDefinition
    labels: dict[str, str]
    tenant_id: str | None
    max_iterations: int
    agent_span: Any
    session_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    session: Any = None
    history_tail_length: int = 0
    model_config: ModelConfig | None = None
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    knowledge_counts: dict[str, int] = field(default_factory=dict)
    #: Set when this invocation resumes a paused tool wave.
    resume: PendingApproval | None = None
    approved: bool = False


class LangGraphRuntime(GovernedRuntime):
    """Micro-Agent runtime whose invocation loop is a LangGraph ``StateGraph``."""

    def __init__(self, config: LangGraphRuntimeConfig | None = None) -> None:
        config = config or LangGraphRuntimeConfig()
        if config.checkpoint_store is not None:
            raise ValueError("LangGraph runtime does not support checkpoint_store")
        super().__init__(config)
        self._graph: Any = None

    # ------------------------------------------------------------------
    # Capabilities and lifecycle
    # ------------------------------------------------------------------

    def capabilities(self) -> RuntimeCapabilities:
        model = self._model_provider.capabilities()
        return RuntimeCapabilities(
            streaming=model.streaming,
            memory=self._config.memory_provider is not None,
            mcp=self._config.mcp_manager is not None,
            a2a=False,
            structured_output=model.structured_output,
            checkpointing=False,
        )

    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        agent = await super().create(definition)
        if self._graph is None:
            self._graph = self._compile_graph()
        agent.capabilities = AgentCapabilities(memory=self._config.memory_provider is not None)
        return agent

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------

    def _compile_graph(self) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:  # pragma: no cover - exercised without the extra
            raise LangGraphError(
                "the LangGraph runtime requires the optional 'langgraph' extra: "
                "pip install 'micro-agents[langgraph]'"
            ) from exc

        graph = StateGraph(_State)
        graph.add_node("prepare", self._node_prepare)
        graph.add_node("model", self._node_model)
        graph.add_node("tools", self._node_tools)
        graph.add_node("finalize", self._node_finalize)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "model")
        graph.add_conditional_edges(
            "model", self._route_after_model, {"tools": "tools", "finalize": "finalize"}
        )
        graph.add_edge("tools", "model")
        graph.add_edge("finalize", END)
        return graph.compile()

    @staticmethod
    def _run_of(config: RunnableConfig) -> _Run:
        run = (config or {}).get("configurable", {}).get("run")
        if not isinstance(run, _Run):  # pragma: no cover - guards misuse of the graph
            raise LangGraphError("graph invoked without a run context")
        return run

    async def _node_prepare(self, state: _State, config: RunnableConfig) -> _State:
        run = self._run_of(config)
        definition = run.definition
        internal = run.agent._internal
        tools: dict[str, Tool] = internal["tools"]
        session_provider: SessionProvider | None = internal["session_provider"]
        session_ttl: int | None = internal["session_ttl"]

        if session_provider is not None and run.session_id:
            session = await run.deadline.run(
                call_with_supported_kwargs(
                    session_provider.get, run.session_id, tenant_id=run.tenant_id
                )
            )
            if session is None:
                session = await run.deadline.run(
                    call_with_supported_kwargs(
                        session_provider.create,
                        run.session_id,
                        ttl_seconds=session_ttl,
                        tenant_id=run.tenant_id,
                    )
                )
            run.session = session

        knowledge_context = ""
        if self._config.knowledge_provider is not None and self._knowledge_refs:
            knowledge_context, run.knowledge_counts = await run.deadline.run(
                retrieve_knowledge_context(
                    self._config.knowledge_provider,
                    build_knowledge_query(run.input_payload),
                    self._knowledge_refs,
                )
            )

        system_prompt = self._system_prompt(definition, internal["skills"])
        if knowledge_context:
            system_prompt += "\n\n" + knowledge_context
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        if run.session is not None:
            tail = run.session.messages[-MAX_SESSION_HISTORY_MESSAGES:]
            run.history_tail_length = len(tail)
            messages.extend(tail)
        messages.append({"role": "user", "content": json.dumps(run.input_payload, default=str)})

        provider: ModelProvider = internal["model_provider"]
        model_ref = definition.spec.dependencies.model
        run.model_config = ModelConfig(
            ref=model_ref.ref if model_ref else "default",
            provider=model_ref.provider if model_ref else None,
            model_id=model_ref.model_id if model_ref else None,
            endpoint=model_ref.endpoint if model_ref else None,
            credential_ref=model_ref.credential_ref if model_ref else None,
            generation=structured_output_generation(
                model_ref.generation if model_ref else {},
                definition.spec.behavior.output_contract,
                provider.capabilities(),
            ),
            timeout_seconds=model_ref.timeout_seconds if model_ref else None,
        )
        run.tool_schemas = [
            {
                "name": tool.metadata.name,
                "description": tool.metadata.description,
                "input_schema": tool.input_schema.parameters,
            }
            for tool in tools.values()
        ]
        iterations = 0
        tool_results: list[dict[str, Any]] = []
        if run.resume is not None:
            # Continue from the paused transcript; the approved wave runs (hard
            # policy denials still apply) or the model is told it was denied.
            replay = await self._replay_pending_approval(
                run.agent,
                tools,
                run.resume,
                run.approved,
                trace_id=run.trace_id,
                parent_span_id=run.agent_span.span_id,
                labels=run.labels,
                deadline=run.deadline,
                side_effect_state=run.side_effect_state,
            )
            messages = replay.messages
            iterations = run.resume.iterations
            tool_results = [*run.resume.all_tool_results, *replay.tool_results]
        return {
            "messages": messages,
            "tool_requests": [],
            "tool_results": tool_results,
            "usage": {},
            "iterations": iterations,
            "content": "",
            "finish_reason": "",
            "max_iterations_reached": False,
        }

    async def _node_model(self, state: _State, config: RunnableConfig) -> _State:
        run = self._run_of(config)
        provider: ModelProvider = run.agent._internal["model_provider"]
        model_config = run.model_config
        assert model_config is not None
        messages = list(state["messages"])
        iterations = state["iterations"] + 1
        tool_schemas = run.tool_schemas or None
        emit_delta = run.emit_delta

        model_span = self._telemetry.start_span(
            "model.generate",
            trace_id=run.trace_id,
            parent_span_id=run.agent_span.span_id,
            attributes={"iteration": iterations},
        )
        model_start = time.monotonic()
        try:
            if emit_delta is None:
                response = await run.deadline.run(
                    provider.generate(model_config, messages, tools=tool_schemas),
                    cap=model_config.timeout_seconds,
                )
            else:

                async def consume_stream() -> ModelResponse:
                    final: ModelResponse | None = None
                    async for event in provider.stream(model_config, messages, tools=tool_schemas):
                        if event.delta:
                            await emit_delta(event.delta)
                        if event.response is not None:
                            final = event.response
                    if final is None:
                        raise RuntimeError("model stream ended without a final response")
                    return final

                response = await run.deadline.run(
                    consume_stream(), cap=model_config.timeout_seconds
                )
        except Exception as exc:
            model_span.add_event("model.error", {"error": str(exc)})
            self._telemetry.finish_span(model_span)
            raise
        latency = round((time.monotonic() - model_start) * 1000, 2)
        model_span.set_attribute("latency_ms", latency)
        model_span.add_event("model.response", {"finish_reason": response.finish_reason})
        self._telemetry.finish_span(model_span)
        self._telemetry.record("model_latency_ms", latency, run.labels)
        self._telemetry.observe_histogram("model_latency_histogram_ms", latency, run.labels)
        self._telemetry.record_model_usage(response.usage, run.labels)
        usage = {k: state["usage"].get(k, 0) + v for k, v in response.usage.items()}

        assistant: dict[str, Any] = {"role": "assistant", "content": response.content}
        tool_requests = [dict(item) for item in response.tool_requests]
        if tool_requests:
            for item in tool_requests:
                item["id"] = str(item.get("id") or new_call_id())
            assistant["tool_calls"] = [
                {
                    "id": item["id"],
                    "type": "function",
                    "function": {
                        "name": str(item.get("name", "")),
                        "arguments": json.dumps(item.get("arguments") or {}, default=str),
                    },
                }
                for item in tool_requests
            ]
        messages.append(assistant)

        reached = bool(tool_requests) and iterations >= run.max_iterations
        if reached:
            self._telemetry.logger.warning(
                "max_iterations reached with pending tool requests",
                request_id=run.request.request_id,
                max_iterations=run.max_iterations,
            )
        return {
            "messages": messages,
            "tool_requests": tool_requests,
            "usage": usage,
            "iterations": iterations,
            "content": response.content,
            "finish_reason": response.finish_reason,
            "max_iterations_reached": reached,
        }

    @staticmethod
    def _route_after_model(state: _State) -> str:
        if not state.get("tool_requests") or state.get("max_iterations_reached"):
            return "finalize"
        return "tools"

    async def _node_tools(self, state: _State, config: RunnableConfig) -> _State:
        run = self._run_of(config)
        tools: dict[str, Tool] = run.agent._internal["tools"]
        if self._approval_needed(run.agent, tools, state["tool_requests"]):
            approval = await self._save_pending_approval(
                run.agent,
                run.request,
                tool_requests=state["tool_requests"],
                messages=state["messages"],
                all_tool_results=state["tool_results"],
                iterations=state["iterations"],
                session_id=run.session_id,
                input_payload=run.input_payload,
            )
            raise ApprovalPausedError(approval)
        results = await self._execute_tools(
            tools,
            state["tool_requests"],
            run.trace_id,
            run.agent_span.span_id,
            run.labels,
            deadline=run.deadline,
            side_effects=run.agent._internal.get("tool_side_effects"),
            side_effect_state=run.side_effect_state,
        )
        messages = list(state["messages"])
        for result in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.get("tool_call_id"),
                    "name": result["tool"],
                    "content": json.dumps(result["error"] or result["output"], default=str),
                }
            )
        return {
            "messages": messages,
            "tool_requests": [],
            "tool_results": [*state["tool_results"], *results],
        }

    async def _node_finalize(self, state: _State, config: RunnableConfig) -> _State:
        run = self._run_of(config)
        internal = run.agent._internal
        session_provider: SessionProvider | None = internal["session_provider"]
        if run.session is not None and session_provider is not None:
            turn_start = 1 + run.history_tail_length
            run.session.messages.extend(dict(m) for m in state["messages"][turn_start:])
            await run.deadline.run(
                session_provider.update(run.session, ttl_seconds=internal["session_ttl"])
            )
        memory_provider = internal["memory_provider"]
        if (
            memory_provider is not None
            and (self._config.memory_policy or MemoryPolicy()).auto_store
        ):
            await run.deadline.run(
                call_with_supported_kwargs(
                    memory_provider.store,
                    MemoryEntry(
                        key=f"invocation:{run.request.request_id or uuid4()}",
                        value={"input": run.input_payload, "output": state["content"]},
                        scope=internal["memory_scope"],
                        tenant_id=run.tenant_id,
                    ),
                )
            )
        return {}

    # ------------------------------------------------------------------
    # Invocation (called by GovernedRuntime's retry/fallback/circuit wrapper)
    # ------------------------------------------------------------------

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
        if request.checkpoint_id is not None:
            raise ValueError("LangGraph runtime does not support checkpoint resume")
        if self._graph is None:  # pragma: no cover - create() compiles the graph
            self._graph = self._compile_graph()

        definition: MicroAgentDefinition = agent._internal["definition"]
        semantics = definition.spec.runtime
        bound_identity = get_invocation_identity()
        tenant_id = (
            bound_identity.user.tenant_id
            if bound_identity is not None and bound_identity.user is not None
            else None
        )
        max_iterations = semantics.max_iterations or self._config.default_max_iterations
        # Approval continuation: unknown, expired or foreign ones fail fast.
        resume: PendingApproval | None = None
        approved = False
        if request.continuation_id:
            resume, approved = await self._load_pending_approval(agent, request)
        session_id = request.session_id or (resume.session_id if resume else None)
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
                "runtime": "langgraph",
            },
        )
        run = _Run(
            agent=agent,
            request=request,
            trace_id=trace_id,
            deadline=deadline,
            side_effect_state=side_effect_state,
            emit_delta=emit_delta,
            definition=definition,
            labels={"agent": definition.metadata.name},
            tenant_id=tenant_id,
            max_iterations=max_iterations,
            agent_span=agent_span,
            session_id=session_id,
            input_payload=resume.input_payload if resume is not None else request.input,
            resume=resume,
            approved=approved,
        )
        started = time.monotonic()
        try:
            final: _State = await self._graph.ainvoke(
                {"iterations": 0},
                config={
                    "configurable": {"run": run},
                    "recursion_limit": max_iterations * _STEPS_PER_ITERATION + _RECURSION_SLACK,
                },
            )
        finally:
            self._telemetry.finish_span(agent_span)

        tool_results = final["tool_results"]
        agent_span.set_attribute("iterations", final["iterations"])
        agent_span.add_event(
            "agent.response",
            {"finish_reason": final["finish_reason"], "tools_called": len(tool_results)},
        )
        return AgentResponse(
            output={"content": final["content"], "tool_results": tool_results},
            request_id=request.request_id,
            session_id=run.session_id,
            status="success",
            metadata={
                "usage": final["usage"],
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "model_ref": run.model_config.ref if run.model_config else "default",
                "tools_called": [r["tool"] for r in tool_results if not r.get("denied")],
                "iterations": final["iterations"],
                "max_iterations_reached": final["max_iterations_reached"],
                "unresolved_tools": agent._internal["unresolved_tools"],
                "knowledge_entries": sum(run.knowledge_counts.values()),
                "knowledge_sources": run.knowledge_counts,
                "resumed_from_checkpoint": None,
                "runtime": "langgraph",
            },
        )
