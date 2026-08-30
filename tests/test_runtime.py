"""Tests for Micro-Agent Runtime SPI."""

import pytest

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
)
from micro_agent.definition import load_definition_from_dict
from micro_agent.runtime import AgentRuntime, RuntimeAgent, RuntimeCapabilities


class TestRuntimeCapabilities:
    """Test runtime capabilities."""

    def test_defaults(self):
        caps = RuntimeCapabilities()
        assert caps.streaming is False
        assert caps.memory is False
        assert caps.mcp is False

    def test_custom(self):
        caps = RuntimeCapabilities(streaming=True, mcp=True)
        assert caps.streaming is True
        assert caps.mcp is True

    def test_supports_and_serializes_matrix(self):
        caps = RuntimeCapabilities(memory=True)
        assert caps.supports("memory") is True
        assert caps.supports("streaming") is False
        assert caps.supports("unknown") is False
        assert caps.as_dict() == {
            "streaming": False,
            "memory": True,
            "mcp": False,
            "a2a": False,
            "structured_output": False,
            "checkpointing": False,
        }


class TestRuntimeAgent:
    """Test runtime agent handle."""

    def test_creation(self):
        identity = AgentIdentity(
            agent_id="test-001",
            agent_name="test-agent",
            agent_version="1.0.0",
        )
        caps = AgentCapabilities(streaming=True)
        agent = RuntimeAgent(identity=identity, capabilities=caps)
        assert agent.identity.agent_id == "test-001"
        assert agent.capabilities.streaming is True
        assert agent._internal is None


class TestAgentRuntimeInterface:
    """Test that AgentRuntime is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            AgentRuntime()  # type: ignore[abstract]


class TestMinimalDefinitionLoading:
    """Test that a minimal definition can be loaded for runtime use."""

    def test_minimal_definition_for_runtime(self):
        data = {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "runtime-test", "version": "0.1.0"},
            "spec": {"behavior": {"instructions": "Test runtime."}},
        }
        definition = load_definition_from_dict(data)
        assert definition.metadata.name == "runtime-test"
        assert definition.spec.behavior.instructions == "Test runtime."
