"""ADK Runtime — vertical slice implementation.

Uses the fake model provider for CI by default; an OpenAI-compatible provider
is selected when configured with a real endpoint. No ADK-native types leak
into definition or core contracts.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
)
from micro_agent.definition import ErrorPolicy, MicroAgentDefinition
from micro_agent.health import DependencyProbe, HealthStatus
from micro_agent.mcp import McpConnectionManager
from micro_agent.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelProvider,
)
from micro_agent.observability import Telemetry
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.security import (
    AgentPolicy,
    Operation,
    OperationRegistry,
    OperationResult,
    PolicyEvaluator,
    build_security_context,
)
from micro_agent.session import SessionProvider
from micro_agent.tools import EchoTool, Tool

_DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
_MAX_SESSION_HISTORY_MESSAGES = 20


@dataclass
class AdkRuntimeConfig:
    """Configuration for the ADK runtime."""

    fake_model_config: FakeModelConfig = field(default_factory=FakeModelConfig)
    model_provider: ModelProvider | None = None
    session_provider: SessionProvider | None = None
    memory_provider: MemoryProvider | None = None
    memory_policy: MemoryPolicy | None = None
    mcp_manager: McpConnectionManager | None = None
    policy: AgentPolicy | None = None
    operation_registry: OperationRegistry | None = None
    telemetry: Telemetry | None = None
    default_max_iterations: int = 5


# Built-in native tool registry. Definition tools are matched by name.
_BUILTIN_TOOLS: dict[str, type[Tool]] = {
    "echo": EchoTool,
}


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
        self._telemetry = self._config.telemetry or Telemetry()
        self._policy_evaluator = (
            PolicyEvaluator(self._config.policy) if self._config.policy is not None else None
        )
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
        healthy = await self._model_provider.health_check()
        if not healthy:
            raise RuntimeError("model provider failed its health check at startup")
        definition: MicroAgentDefinition = agent._internal["definition"]
        # Deterministic platform policy: denied MCP servers fail startup.
        if self._policy_evaluator is not None:
            for server in definition.spec.dependencies.mcp_servers:
                decision = self._policy_evaluator.evaluate_mcp(server.ref)
                if not decision.allowed:
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
        """Release runtime-level resources (HTTP connection pools, MCP)."""
        aclose = getattr(self._model_provider, "aclose", None)
        if aclose is not None:
            await aclose()
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

        async def _mcp_probe() -> HealthStatus | bool:
            assert self._config.mcp_manager is not None
            return await self._config.mcp_manager.health_probe()

        if self._config.session_provider is not None:
            probes["session"] = _session_probe
        if self._config.memory_provider is not None:
            probes["memory"] = _memory_probe
        if self._config.mcp_manager is not None:
            probes["mcp"] = _mcp_probe
        return probes

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        semantics = definition.spec.runtime
        trace_id = request.request_id or str(uuid4())
        labels = {"agent": definition.metadata.name}

        async def run_once() -> AgentResponse:
            inner = self._invoke_inner(agent, request, trace_id)
            if semantics.timeout_seconds:
                return await asyncio.wait_for(inner, semantics.timeout_seconds)
            return await inner

        start_time = time.monotonic()
        try:
            response = await run_once()
        except Exception as exc:
            self._telemetry.increment("agent_invocation_errors_total", labels)
            self._telemetry.logger.error(
                "invocation failed",
                request_id=request.request_id,
                session_id=request.session_id,
                error=str(exc),
            )
            if semantics.error_policy is ErrorPolicy.RETRY:
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
        self, agent: RuntimeAgent, request: AgentRequest, trace_id: str
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

        agent_span = self._telemetry.start_span(
            "agent.invoke",
            trace_id=trace_id,
            attributes={
                "agent": definition.metadata.name,
                "session_id": request.session_id,
            },
        )

        session = None
        if session_provider is not None and request.session_id:
            session = await session_provider.get(request.session_id) or (
                await session_provider.create(request.session_id, ttl_seconds=session_ttl)
            )

        inner_start = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(definition, skills)}
        ]
        if session is not None:
            messages.extend(session.messages[-_MAX_SESSION_HISTORY_MESSAGES:])
        messages.append({"role": "user", "content": json.dumps(request.input, default=str)})

        model_ref = definition.spec.dependencies.model
        model_config = ModelConfig(
            ref=model_ref.ref if model_ref else "default",
            provider=model_ref.provider if model_ref else None,
            endpoint=model_ref.endpoint if model_ref else None,
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
                if model_config.timeout_seconds:
                    response = await asyncio.wait_for(model_call, model_config.timeout_seconds)
                else:
                    response = await model_call
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
            self._telemetry.record("model_tokens_total", sum(response.usage.values()), labels)

            messages.append({"role": "assistant", "content": response.content})

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

            tool_results = await self._execute_tools(
                tools, response.tool_requests, trace_id, agent_span.span_id, labels
            )
            all_tool_results.extend(tool_results)
            for result in tool_results:
                messages.append(
                    {
                        "role": "tool",
                        "name": result["tool"],
                        "content": json.dumps(result["error"] or result["output"], default=str),
                    }
                )

        if session is not None:
            session.messages.append(
                {"role": "user", "content": json.dumps(request.input, default=str)}
            )
            session.messages.append({"role": "assistant", "content": response.content})
            await session_provider.update(session, ttl_seconds=session_ttl)  # type: ignore[union-attr]

        if (
            memory_provider is not None
            and (self._config.memory_policy or MemoryPolicy()).auto_store
        ):
            await memory_provider.store(
                MemoryEntry(
                    key=f"invocation:{request.request_id or uuid4()}",
                    value={
                        "input": request.input,
                        "output": response.content,
                    },
                    scope=memory_scope,
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
            session_id=request.session_id,
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

    def _deny(self, labels: dict[str, str], tool_name: str, reason: str) -> None:
        self._telemetry.increment("policy_denials_total", {**labels, "tool": tool_name})
        self._telemetry.logger.warning("tool denied by policy", tool=tool_name, reason=reason)

    def _resolve_tools(self, definition: MicroAgentDefinition) -> tuple[dict[str, Tool], list[str]]:
        """Resolve definition-declared tools against the built-in registry."""
        tools: dict[str, Tool] = {}
        unresolved: list[str] = []
        for tool_def in definition.spec.dependencies.tools:
            tool_cls = _BUILTIN_TOOLS.get(tool_def.name)
            if tool_cls is None:
                unresolved.append(tool_def.name)
                continue
            tools[tool_def.name] = tool_cls()
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
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for tool_request in tool_requests:
            tool_name = tool_request.get("name", "")
            tool = tools.get(tool_name)
            if tool is None:
                results.append(
                    {
                        "tool": tool_name,
                        "output": None,
                        "error": f"unknown tool: {tool_name}",
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
                            "output": None,
                            "error": f"denied by policy: {tool_decision.reason}",
                            "latency_ms": 0.0,
                            "denied": True,
                        }
                    )
                    continue
                side_effect_decision = self._policy_evaluator.evaluate_side_effect(tool_name)
                if not side_effect_decision.allowed:
                    self._deny(labels, tool_name, side_effect_decision.reason)
                    results.append(
                        {
                            "tool": tool_name,
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
            arguments = tool_request.get("arguments", {})
            registry = self._config.operation_registry
            operation = None
            if registry is not None:
                operation = _new_operation(tool_name, arguments)
                if registry.is_duplicate(operation):
                    prior = registry.find_by_idempotency_key(operation.idempotency_key or "")
                    results.append(
                        {
                            "tool": tool_name,
                            "output": prior.output if prior else None,
                            "error": None,
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
                result = await asyncio.wait_for(tool.execute(arguments), timeout)
                output: Any = result.output
                error: str | None = result.error
                if result.is_error and not error:
                    error = "tool reported an error"
            except TimeoutError:
                output = None
                error = f"tool '{tool_name}' timed out"
            except Exception as exc:  # noqa: BLE001 — tool failures become results
                output = None
                error = str(exc)

            if registry is not None and operation is not None:
                registry.record(
                    operation,
                    OperationResult(
                        operation_id=operation.operation_id,
                        output=output,
                        error=error,
                    ),
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
                    "output": output,
                    "error": error,
                    "latency_ms": tool_latency,
                }
            )
        return results


def _new_operation(tool_name: str, arguments: dict[str, Any]) -> Operation:
    """Build an Operation for a tool call; arguments may carry an idempotency key."""
    key = arguments.get("idempotency_key")
    return Operation(
        name=tool_name,
        arguments=arguments,
        idempotency_key=str(key) if key else None,
    )
