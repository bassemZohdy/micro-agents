"""ADK Runtime — initial vertical slice implementation.

Uses the fake model provider for CI. No ADK-native types leak into
definition or core contracts.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelConfig
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities
from micro_agent.tools import EchoTool, Tool


@dataclass
class AdkRuntimeConfig:
    """Configuration for the ADK runtime."""

    fake_model_config: FakeModelConfig = field(default_factory=FakeModelConfig)


class AdkRuntime(AgentRuntime):
    """ADK runtime implementation.

    Initial vertical slice using the fake model provider.
    ADK types do not leak through the public interface.
    """

    def __init__(self, config: AdkRuntimeConfig | None = None) -> None:
        self._config = config or AdkRuntimeConfig()
        self._model_provider = FakeModelProvider(self._config.fake_model_config)
        self._started = False

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            streaming=False,
            memory=True,
            mcp=False,
            a2a=False,
            structured_output=False,
        )

    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        identity = AgentIdentity(
            agent_id=f"{definition.metadata.name}-{definition.metadata.version}",
            agent_name=definition.metadata.name,
            agent_version=definition.metadata.version,
        )
        capabilities = AgentCapabilities(memory=True)

        tools: dict[str, Tool] = {}
        for tool_def in definition.spec.dependencies.tools:
            if tool_def.name == "echo":
                tools["echo"] = EchoTool()

        return RuntimeAgent(
            identity=identity,
            capabilities=capabilities,
            _internal={
                "definition": definition,
                "model_provider": self._model_provider,
                "tools": tools,
                "started": False,
            },
        )

    async def start(self, agent: RuntimeAgent) -> None:
        agent._internal["started"] = True
        self._started = True

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        start_time = time.monotonic()
        definition: MicroAgentDefinition = agent._internal["definition"]
        model_provider: FakeModelProvider = agent._internal["model_provider"]
        tools: dict[str, Tool] = agent._internal["tools"]

        model_config = ModelConfig(
            ref=definition.spec.dependencies.model.ref
            if definition.spec.dependencies.model
            else "default"
        )

        messages = [
            {"role": "system", "content": definition.spec.behavior.instructions},
            {"role": "user", "content": json.dumps(request.input, default=str)},
        ]

        tool_schemas = []
        for tool in tools.values():
            tool_schemas.append(
                {
                    "name": tool.metadata.name,
                    "description": tool.metadata.description,
                    "input_schema": tool.input_schema.parameters,
                }
            )

        response = await model_provider.generate(model_config, messages, tools=tool_schemas or None)

        tool_results = []
        for tool_request in response.tool_requests:
            tool_name = tool_request.get("name", "")
            if tool_name in tools:
                tool_start = time.monotonic()
                result = await tools[tool_name].execute(tool_request.get("arguments", {}))
                tool_latency = time.monotonic() - tool_start
                tool_results.append(
                    {
                        "tool": tool_name,
                        "output": result.output,
                        "error": result.error,
                        "latency_ms": round(tool_latency * 1000, 2),
                    }
                )

        latency_ms = round((time.monotonic() - start_time) * 1000, 2)

        return AgentResponse(
            output={"content": response.content, "tool_results": tool_results},
            request_id=request.request_id,
            session_id=request.session_id,
            status="success",
            metadata={
                "usage": response.usage,
                "latency_ms": latency_ms,
                "model_ref": model_config.ref,
                "tools_called": [r["tool"] for r in tool_results],
            },
        )

    async def stop(self, agent: RuntimeAgent) -> None:
        agent._internal["started"] = False
        self._started = False

    async def shutdown(self, agent: RuntimeAgent) -> None:
        agent._internal = None
