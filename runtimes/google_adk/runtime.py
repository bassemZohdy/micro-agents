"""Runtime-neutral adapter for Google's Agent Development Kit (ADK).

Google ADK is imported lazily so the core package remains usable without the
optional ``google-adk`` extra.  Only :class:`RuntimeAgent` and the Micro-Agent
request/response contracts cross this module's public boundary; ADK objects
are retained in the opaque runtime-agent handle.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from micro_agent.core import AgentCapabilities, AgentIdentity, AgentRequest, AgentResponse
from micro_agent.definition import MicroAgentDefinition
from micro_agent.health import DependencyProbe
from micro_agent.models import ModelConfig, ModelProvider
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.tools import EchoTool, Tool


class GoogleAdkError(RuntimeError):
    """Raised when Google ADK cannot be loaded or configured."""


@dataclass
class GoogleAdkRuntimeConfig:
    """Configuration for the Google ADK adapter.

    ``model_factory`` and the service factories are dependency-injection seams
    for tests and deployments that already own ADK services.  When omitted,
    the adapter uses an ADK in-memory session service and a model string from
    the definition.  A Micro-Agent ``ModelProvider`` can be supplied to bridge
    existing providers (including the deterministic fake provider) through
    ADK's ``BaseLlm`` interface.
    """

    model_provider: ModelProvider | None = None
    model_factory: Callable[[MicroAgentDefinition], Any] | None = None
    session_service_factory: Callable[[], Any] | None = None
    runner_factory: Callable[..., Any] | None = None
    tool_registry: dict[str, Tool] = field(default_factory=dict)
    app_name_prefix: str = "micro-agent"
    user_id: str = "micro-agent-user"


_BUILTIN_TOOLS: dict[str, type[Tool]] = {"echo": EchoTool}


class GoogleAdkRuntime(AgentRuntime):
    """Google ADK implementation behind the runtime-neutral SPI."""

    def __init__(self, config: GoogleAdkRuntimeConfig | None = None) -> None:
        self._config = config or GoogleAdkRuntimeConfig()
        self._runners: list[Any] = []
        self._model_provider = self._config.model_provider

    def capabilities(self) -> RuntimeCapabilities:
        """Report only capabilities implemented by this adapter path."""
        return RuntimeCapabilities(
            streaming=False,
            memory=False,
            mcp=False,
            a2a=False,
            structured_output=False,
            checkpointing=False,
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
            generation=model_ref.generation if model_ref else {},
            timeout_seconds=model_ref.timeout_seconds if model_ref else None,
        )
        adk_model = self._adk_model(definition, model_config)
        tools = self._resolve_tools(definition)
        adk_tools = [_as_adk_tool(tool) for tool in tools.values()]
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
            else session_service_cls()
        )
        if self._config.runner_factory is not None:
            runner = self._config.runner_factory(
                agent=adk_agent,
                app_name=app_name,
                session_service=session_service,
            )
        else:
            runner = runner_cls(
                agent=adk_agent,
                app_name=app_name,
                session_service=session_service,
            )
        self._runners.append(runner)

        identity = AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        )
        return RuntimeAgent(
            identity=identity,
            capabilities=AgentCapabilities(),
            _internal={
                "definition": definition,
                "adk_agent": adk_agent,
                "adk_runner": runner,
                "adk_session_service": session_service,
                "app_name": app_name,
                "user_id": self._config.user_id,
                "model_config": model_config,
                "started": False,
            },
        )

    async def start(self, agent: RuntimeAgent) -> None:
        """Check the injected provider before declaring the ADK agent ready."""
        if self._model_provider is not None and not await self._model_provider.health_check():
            raise RuntimeError("model provider failed its health check at startup")
        agent._internal["started"] = True

    async def stop(self, agent: RuntimeAgent) -> None:
        """Stop accepting work while leaving runner cleanup to ``close``."""
        agent._internal["started"] = False

    async def shutdown(self, agent: RuntimeAgent) -> None:
        """Release the opaque ADK handle after the agent has stopped."""
        agent._internal = None

    async def close(self) -> None:
        """Close ADK runners and any injected model provider resources."""
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
        if self._model_provider is not None:
            close = getattr(self._model_provider, "aclose", None)
            if close is not None:
                await close()

    def health_probes(self) -> dict[str, DependencyProbe]:
        """Expose the injected model provider as the adapter's active probe."""
        if self._model_provider is None:
            return {}
        return {"model": self._model_provider.health_check}

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        """Invoke ADK's runner and translate the terminal model event."""
        if agent._internal is None:
            raise RuntimeError("runtime agent has been shut down")
        definition: MicroAgentDefinition = agent._internal["definition"]
        runner = agent._internal["adk_runner"]
        service = agent._internal["adk_session_service"]
        app_name = agent._internal["app_name"]
        user_id = agent._internal["user_id"]
        session_id = request.session_id or str(uuid4())

        async def run() -> AgentResponse:
            await _ensure_session(service, app_name, user_id, session_id)
            content = _user_content(request.input)
            events: list[Any] = []
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                invocation_id=request.request_id or None,
                new_message=content,
            ):
                events.append(event)
            output = _terminal_text(events, agent._internal["adk_agent"].name)
            return AgentResponse(
                output={
                    "content": output,
                    "tool_results": _tool_results_from_events(events),
                },
                request_id=request.request_id,
                session_id=session_id,
                status="success",
                metadata={"runtime": "google-adk", "event_count": len(events)},
            )

        timeout = _shortest_timeout(
            definition.spec.runtime.timeout_seconds,
            request.timeout_seconds,
        )
        if timeout is None:
            return await run()
        return await asyncio.wait_for(run(), timeout=timeout)

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
        return model

    def _resolve_tools(self, definition: MicroAgentDefinition) -> dict[str, Tool]:
        tools = dict(self._config.tool_registry)
        for tool_definition in definition.spec.dependencies.tools:
            if tool_definition.name in tools:
                continue
            tool_class = _BUILTIN_TOOLS.get(tool_definition.name)
            if tool_class is not None:
                tools[tool_definition.name] = tool_class()
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
            response = await self._provider.generate(self._config, messages, tools=tools or None)
            parts: list[Any] = []
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
            yield LlmResponse(
                content=types.Content(role="model", parts=parts),
                finish_reason=cast(Any, response.finish_reason),
            )

    return ProviderLlm(provider, config)


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


def _as_adk_tool(tool: Tool) -> Any:
    """Adapt a Micro-Agent tool to ADK's client-side ``BaseTool`` contract."""
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

        def _get_declaration(self) -> Any:
            schema = self._micro_tool.input_schema.parameters
            return types.FunctionDeclaration(
                name=self.name,
                description=self.description,
                parameters=schema or None,  # type: ignore[arg-type]
            )

        async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
            result = await self._micro_tool.execute(args)
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


def _user_content(payload: dict[str, Any]) -> Any:
    try:
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - guarded by ``_load_adk``
        raise GoogleAdkError("Google GenAI content types are unavailable") from exc
    return types.Content(
        role="user",
        parts=[types.Part.from_text(text=json.dumps(payload, default=str))],
    )


def _terminal_text(events: list[Any], agent_name: str) -> str:
    for event in reversed(events):
        if getattr(event, "author", None) != agent_name:
            continue
        content = getattr(event, "content", None)
        text = "".join(
            str(part.text)
            for part in (getattr(content, "parts", []) or [])
            if getattr(part, "text", None)
        )
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
