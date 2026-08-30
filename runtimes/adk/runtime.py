"""ADK Runtime — initial vertical slice implementation.

Uses the fake model provider for CI. No ADK-native types leak into
definition or core contracts.
"""

from __future__ import annotations

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

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            streaming=False,
            memory=False,
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
        capabilities = AgentCapabilities()
        return RuntimeAgent(
            identity=identity,
            capabilities=capabilities,
            _internal={
                "definition": definition,
                "model_provider": self._model_provider,
            },
        )

    async def start(self, agent: RuntimeAgent) -> None:
        pass

    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        definition: MicroAgentDefinition = agent._internal["definition"]
        model_provider: FakeModelProvider = agent._internal["model_provider"]

        model_config = ModelConfig(
            ref=definition.spec.dependencies.model.ref
            if definition.spec.dependencies.model
            else "default"
        )

        messages = [
            {"role": "system", "content": definition.spec.behavior.instructions},
            {"role": "user", "content": str(request.input)},
        ]

        response = await model_provider.generate(model_config, messages)

        return AgentResponse(
            output={"content": response.content},
            request_id=request.request_id,
            session_id=request.session_id,
            status="success",
            metadata={"usage": response.usage},
        )

    async def stop(self, agent: RuntimeAgent) -> None:
        pass

    async def shutdown(self, agent: RuntimeAgent) -> None:
        agent._internal = None
