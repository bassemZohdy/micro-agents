"""Tests for ADK Runtime vertical slice."""

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelConfig
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


class TestAdkRuntime:
    """Test ADK runtime vertical slice."""

    @pytest.fixture
    def definition(self):
        return load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "test-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "You are a test agent."},
                    "dependencies": {"model": {"ref": "fake-model"}},
                },
            }
        )

    @pytest.mark.asyncio
    async def test_capabilities(self):
        runtime = AdkRuntime()
        caps = runtime.capabilities()
        assert caps.streaming is False

    @pytest.mark.asyncio
    async def test_create_agent(self, definition):
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        assert agent.identity.agent_name == "test-agent"
        assert agent.identity.agent_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_invoke(self, definition):
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        request = AgentRequest(input={"action": "test"})
        response = await runtime.invoke(agent, request)
        assert response.status == "success"
        assert response.output["content"] == "fake response"

    @pytest.mark.asyncio
    async def test_invoke_custom_response(self, definition):
        config = AdkRuntimeConfig(fake_model_config=FakeModelConfig(response="custom answer"))
        runtime = AdkRuntime(config)
        agent = await runtime.create(definition)
        request = AgentRequest(input={"action": "test"})
        response = await runtime.invoke(agent, request)
        assert response.output["content"] == "custom answer"

    @pytest.mark.asyncio
    async def test_lifecycle(self, definition):
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        await runtime.start(agent)
        request = AgentRequest(input={"action": "test"})
        response = await runtime.invoke(agent, request)
        assert response.status == "success"
        await runtime.stop(agent)
        await runtime.shutdown(agent)
        assert agent._internal is None

    @pytest.mark.asyncio
    async def test_no_adk_types_leak(self, definition):
        runtime = AdkRuntime()
        agent = await runtime.create(definition)
        serialized = str(agent.identity)
        adk_indicators = ["google.adk", "adk_agent", "LlmAgent"]
        for indicator in adk_indicators:
            assert indicator not in serialized
