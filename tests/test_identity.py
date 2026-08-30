"""Tests for Micro-Agent Identity and Security Context."""

import pytest

from micro_agent.observability import (
    AgentIdentity,
    CallerIdentity,
    RuntimeIdentity,
    SecurityContext,
    UserContext,
)


class TestAgentIdentity:
    """Test agent identity."""

    def test_creation(self):
        identity = AgentIdentity(agent_id="a1", agent_name="test", agent_version="1.0")
        assert identity.agent_id == "a1"
        assert identity.namespace == "default"

    def test_frozen(self):
        identity = AgentIdentity(agent_id="a1", agent_name="test", agent_version="1.0")
        with pytest.raises(AttributeError):
            identity.agent_id = "changed"


class TestCallerIdentity:
    """Test caller identity."""

    def test_creation(self):
        caller = CallerIdentity(caller_id="user-1")
        assert caller.caller_type == "user"

    def test_service_caller(self):
        caller = CallerIdentity(caller_id="svc-1", caller_type="service")
        assert caller.caller_type == "service"


class TestUserContext:
    """Test user context."""

    def test_basic(self):
        ctx = UserContext(user_id="u1")
        assert ctx.tenant_id is None
        assert ctx.roles == []

    def test_with_roles(self):
        ctx = UserContext(user_id="u1", tenant_id="t1", roles=["admin"])
        assert "admin" in ctx.roles


class TestRuntimeIdentity:
    """Test runtime identity."""

    def test_creation(self):
        rid = RuntimeIdentity(workload_id="pod-123")
        assert rid.namespace == "default"


class TestSecurityContext:
    """Test security context."""

    def test_basic(self):
        agent_id = AgentIdentity(agent_id="a1", agent_name="test", agent_version="1.0")
        ctx = SecurityContext(agent_identity=agent_id)
        assert ctx.has_caller() is False
        assert ctx.has_user_context() is False

    def test_with_caller(self):
        agent_id = AgentIdentity(agent_id="a1", agent_name="test", agent_version="1.0")
        caller = CallerIdentity(caller_id="u1")
        ctx = SecurityContext(agent_identity=agent_id, caller_identity=caller)
        assert ctx.has_caller() is True

    def test_with_user_context(self):
        agent_id = AgentIdentity(agent_id="a1", agent_name="test", agent_version="1.0")
        user = UserContext(user_id="u1")
        ctx = SecurityContext(agent_identity=agent_id, user_context=user)
        assert ctx.has_user_context() is True

    def test_agent_identity_not_user_identity(self):
        agent_id = AgentIdentity(agent_id="agent-1", agent_name="test", agent_version="1.0")
        user = UserContext(user_id="user-999")
        ctx = SecurityContext(agent_identity=agent_id, user_context=user)
        assert ctx.agent_identity.agent_id != ctx.user_context.user_id
