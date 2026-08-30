"""Tests for Micro-Agent Core Programming Model."""

import pytest

from micro_agent.core import (
    AgentCapabilities,
    AgentContext,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    AgentState,
    MicroAgent,
)


class TestAgentIdentity:
    """Test agent identity."""

    def test_identity_creation(self):
        identity = AgentIdentity(
            agent_id="test-001",
            agent_name="test-agent",
            agent_version="1.0.0",
        )
        assert identity.agent_id == "test-001"
        assert identity.agent_name == "test-agent"
        assert identity.agent_version == "1.0.0"
        assert identity.namespace == "default"

    def test_identity_frozen(self):
        identity = AgentIdentity(
            agent_id="test-001",
            agent_name="test-agent",
            agent_version="1.0.0",
        )
        with pytest.raises(AttributeError):
            identity.agent_id = "changed"


class TestAgentCapabilities:
    """Test agent capabilities."""

    def test_defaults(self):
        caps = AgentCapabilities()
        assert caps.streaming is False
        assert caps.structured_output is False
        assert caps.memory is False

    def test_custom(self):
        caps = AgentCapabilities(streaming=True, memory=True)
        assert caps.streaming is True
        assert caps.memory is True


class TestAgentRequest:
    """Test agent request."""

    def test_default_request(self):
        req = AgentRequest()
        assert req.request_id
        assert req.input == {}
        assert req.session_id is None

    def test_request_with_input(self):
        req = AgentRequest(input={"action": "check"}, session_id="sess-1")
        assert req.input["action"] == "check"
        assert req.session_id == "sess-1"


class TestAgentResponse:
    """Test agent response."""

    def test_default_response(self):
        resp = AgentResponse()
        assert resp.status == "success"
        assert resp.output == {}
        assert resp.error is None

    def test_error_response(self):
        resp = AgentResponse(status="error", error="something failed")
        assert resp.status == "error"
        assert resp.error == "something failed"


class TestAgentState:
    """Test agent lifecycle states."""

    def test_states_exist(self):
        assert AgentState.CREATED == "created"
        assert AgentState.INITIALIZED == "initialized"
        assert AgentState.READY == "ready"
        assert AgentState.RUNNING == "running"
        assert AgentState.STOPPED == "stopped"
        assert AgentState.ERROR == "error"


class TestMicroAgentInterface:
    """Test that MicroAgent is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            MicroAgent()  # type: ignore[abstract]


class TestAgentContext:
    """Test agent context."""

    def test_context_creation(self):
        identity = AgentIdentity(
            agent_id="test-001",
            agent_name="test-agent",
            agent_version="1.0.0",
        )
        caps = AgentCapabilities(streaming=True)
        ctx = AgentContext(identity=identity, capabilities=caps)
        assert ctx.identity.agent_id == "test-001"
        assert ctx.capabilities.streaming is True
