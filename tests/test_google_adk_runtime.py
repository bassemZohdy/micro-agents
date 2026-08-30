"""Google ADK adapter tests using the runtime-neutral model provider seam."""

from __future__ import annotations

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import ModelConfig, ModelProvider, ModelResponse
from runtimes.google_adk import GoogleAdkError, GoogleAdkRuntime, GoogleAdkRuntimeConfig

pytest.importorskip("google.adk")

pytestmark = pytest.mark.adk


def _definition(*, provider: str | None = None, include_tool: bool = False):
    model = {"ref": "test-model"}
    if provider is not None:
        model["provider"] = provider
    dependencies: dict[str, object] = {"model": model}
    if include_tool:
        dependencies["tools"] = [{"name": "echo", "source": "native"}]
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "adk-test-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Use the declared tools safely."},
                "dependencies": dependencies,
            },
        }
    )


class SequencedProvider(ModelProvider):
    """Provider double that emits one ADK tool call, then a final answer."""

    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[list[dict[str, object]]] = []

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            return ModelResponse(
                tool_requests=[{"name": "echo", "arguments": {"message": "from-adk"}}]
            )
        return ModelResponse(content="completed by ADK")

    async def health_check(self) -> bool:
        return True


class UnhealthyProvider(ModelProvider):
    async def generate(self, config, messages, tools=None):
        return ModelResponse(content="unreachable")

    async def health_check(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_constructs_native_adk_agent_and_invokes_provider():
    from google.adk.agents import LlmAgent

    from micro_agent.models import FakeModelConfig, FakeModelProvider

    runtime = GoogleAdkRuntime(
        GoogleAdkRuntimeConfig(
            model_provider=FakeModelProvider(FakeModelConfig(response="hello from ADK"))
        )
    )
    agent = await runtime.create(_definition(include_tool=True))
    try:
        assert isinstance(agent._internal["adk_agent"], LlmAgent)
        assert agent._internal["adk_agent"].name == "micro_agent_adk_test_agent"
        assert len(agent._internal["adk_agent"].tools) == 1
        await runtime.start(agent)
        response = await runtime.invoke(
            agent,
            AgentRequest(input={"message": "hello"}, session_id="session-1"),
        )
        assert response.output["content"] == "hello from ADK"
        assert response.session_id == "session-1"
        assert response.metadata["runtime"] == "google-adk"
        session = await agent._internal["adk_session_service"].get_session(
            app_name=agent._internal["app_name"],
            user_id=agent._internal["user_id"],
            session_id="session-1",
        )
        assert session is not None
        assert session.events
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_tool_call_preserves_result_and_provider_tool_id():
    provider = SequencedProvider()
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
    agent = await runtime.create(_definition(include_tool=True))
    try:
        response = await runtime.invoke(agent, AgentRequest(input={"message": "run"}))
        assert response.output["content"] == "completed by ADK"
        assert response.output["tool_results"][0]["tool"] == "echo"
        assert response.output["tool_results"][0]["output"] == {"echoed": "from-adk"}
        assert provider.calls == 2
        assert provider.messages[1][-1]["role"] == "tool"
        assert provider.messages[1][-1]["tool_call_id"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_adk_start_checks_injected_provider_health():
    runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=UnhealthyProvider()))
    agent = await runtime.create(_definition())
    with pytest.raises(RuntimeError, match="health check"):
        await runtime.start(agent)
    await runtime.close()


def test_native_adk_rejects_non_google_provider_without_injected_model():
    runtime = GoogleAdkRuntime()
    with pytest.raises(GoogleAdkError, match="native ADK model"):
        # Creation is async only because the SPI is async; no provider call is made.
        import asyncio

        asyncio.run(runtime.create(_definition(provider="openai-compatible")))
