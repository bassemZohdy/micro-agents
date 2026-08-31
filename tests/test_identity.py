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


class TestCallerMetadataIsNotIdentity:
    """Caller-supplied metadata is never treated as identity.

    Identity must come from a configured transport authenticator (open work);
    request metadata is untrusted data, not a principal.
    """

    def test_no_production_module_derives_identity_from_caller_metadata(self):
        from pathlib import Path

        root = Path(__file__).parent.parent
        offenders: list[str] = []
        identity_markers = ("CallerIdentity(", "UserContext(", "RuntimeIdentity(")
        for package in ("micro_agent", "runtimes"):
            for path in (root / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "caller_metadata" in text and any(marker in text for marker in identity_markers):
                    offenders.append(str(path))
        assert offenders == []

    @pytest.mark.asyncio
    async def test_spoofed_caller_metadata_does_not_populate_security_context(self):
        from micro_agent.core import AgentRequest
        from micro_agent.definition import load_definition_from_dict
        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "identity-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test agent."},
                    "dependencies": {"model": {"ref": "fake-model"}},
                    "security": {"identity_requirements": {"require_caller_identity": True}},
                },
            }
        )
        runtime = AdkRuntime(
            AdkRuntimeConfig(model_provider=FakeModelProvider(FakeModelConfig(response="ok")))
        )
        agent = await runtime.create(definition)
        request = AgentRequest(
            input={},
            caller_metadata={
                "user_id": "attacker",
                "tenant_id": "tenant-1",
                "roles": ["admin"],
                "caller_id": "forged-service",
            },
        )
        context = agent._internal["security_context"]
        assert context.caller_identity is None
        assert context.user_context is None
        assert context.runtime_identity is None
        # Identity requirements remain a declaration; they never validate
        # request-supplied metadata.
        assert definition.spec.security.identity_requirements == {"require_caller_identity": True}
        assert request.caller_metadata["user_id"] == "attacker"
