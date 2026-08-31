"""Verified identity propagation through model, tool, and MCP operations."""

from __future__ import annotations

import pytest

from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelConfig, FakeModelProvider, ModelResponse
from micro_agent.security import (
    CallerIdentity,
    InvocationIdentity,
    UserContext,
    get_invocation_identity,
    invocation_identity,
    resolve_workload_identity,
)
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


def _definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "propagation-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "tools": [{"name": "echo", "source": "native"}],
                },
            },
        }
    )


class IdentityCapturingProvider(FakeModelProvider):
    """Captures the identity visible inside model calls."""

    def __init__(self, config: FakeModelConfig) -> None:
        super().__init__(config)
        self.seen: list[InvocationIdentity | None] = []

    async def generate(self, config, messages, tools=None):
        self.seen.append(get_invocation_identity())
        return ModelResponse(content="done")


class TestContextSemantics:
    """Context-variable binding semantics."""

    def test_default_is_none(self):
        assert get_invocation_identity() is None

    def test_context_manager_binds_and_resets(self):
        identity = InvocationIdentity(caller=CallerIdentity(caller_id="c-1"))
        with invocation_identity(identity):
            assert get_invocation_identity() is identity
        assert get_invocation_identity() is None

    @pytest.mark.asyncio
    async def test_binding_does_not_leak_across_awaits(self):
        async def operation() -> InvocationIdentity | None:
            return get_invocation_identity()

        with invocation_identity(InvocationIdentity(caller=CallerIdentity(caller_id="c-2"))):
            seen = await operation()
        assert seen is not None
        assert get_invocation_identity() is None


class TestRuntimePropagation:
    """The runtime binds verified identity for model and tool operations."""

    @pytest.mark.asyncio
    async def test_identity_visible_inside_model_and_tool_operations(self):
        captured: dict[str, object] = {}

        from micro_agent.tools import EchoTool

        class SpyEchoTool(EchoTool):
            async def execute(self, arguments):
                captured["tool"] = get_invocation_identity()
                return await super().execute(arguments)

        class OneToolProvider(IdentityCapturingProvider):
            async def generate(self, config, messages, tools=None):
                self.seen.append(get_invocation_identity())
                if len(self.seen) == 1:
                    return ModelResponse(
                        tool_requests=[{"name": "echo", "arguments": {"message": "x"}}]
                    )
                return ModelResponse(content="done")

        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=OneToolProvider(FakeModelConfig(response="done")),
                tool_registry={"echo": SpyEchoTool()},
                fake_model_config=FakeModelConfig(),
            )
        )
        agent = DefaultMicroAgent(_definition(), runtime)
        request = AgentRequest(
            input={},
            caller_identity=CallerIdentity(caller_id="caller-7", caller_type="service"),
            user_context=UserContext(user_id="alice", tenant_id="t-1"),
        )
        try:
            await agent.initialize()
            await agent.start()
            await agent.invoke(request)
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

        model_identity = runtime._model_provider.seen[0]
        assert model_identity is not None
        assert model_identity.caller is not None
        assert model_identity.caller.caller_id == "caller-7"
        assert model_identity.user is not None
        assert model_identity.user.tenant_id == "t-1"
        tool_identity = captured["tool"]
        assert isinstance(tool_identity, InvocationIdentity)
        assert tool_identity.caller is not None
        assert tool_identity.caller.caller_id == "caller-7"
        assert tool_identity.workload is not None
        assert tool_identity.workload.workload_id
        # The binding is scoped to the invocation.
        assert get_invocation_identity() is None

    @pytest.mark.asyncio
    async def test_identity_reset_when_invocation_fails(self):
        class ExplodingProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                raise RuntimeError("model exploded")

        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=ExplodingProvider(FakeModelConfig())))
        agent = DefaultMicroAgent(_definition(), runtime)
        try:
            await agent.initialize()
            await agent.start()
            with pytest.raises(Exception):  # noqa: B017 - any failure must still reset
                await agent.invoke(
                    AgentRequest(input={}, caller_identity=CallerIdentity(caller_id="c-3"))
                )
            assert get_invocation_identity() is None
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()

    @pytest.mark.asyncio
    async def test_unauthenticated_invocation_still_binds_workload(self):
        provider = IdentityCapturingProvider(FakeModelConfig(response="done"))
        runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
        agent = DefaultMicroAgent(_definition(), runtime)
        try:
            await agent.initialize()
            await agent.start()
            await agent.invoke(AgentRequest(input={}))
        finally:
            await agent.stop()
            await agent.shutdown()
            await runtime.close()
        seen = provider.seen[0]
        assert seen is not None
        assert seen.caller is None
        assert seen.workload is not None


class TestMcpPathPropagation:
    """Identity is visible inside MCP tool execution."""

    @pytest.mark.asyncio
    async def test_mcp_tool_execution_sees_invocation_identity(self):
        from micro_agent.mcp import McpConnectionManager
        from micro_agent.mcp.client import FakeMcpClient
        from micro_agent.mcp.mcp import McpTool

        seen: list[InvocationIdentity | None] = []

        def handler(arguments):
            seen.append(get_invocation_identity())
            return {"ok": True}

        client = FakeMcpClient(
            tools=[McpTool(name="lookup", input_schema={"type": "object"})],
            handlers={"lookup": handler},
        )

        def factory(config):
            return client

        manager = McpConnectionManager(client_factory=factory)
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "mcp-prop-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "Test agent."},
                    "dependencies": {
                        "model": {"ref": "fake-model"},
                        "mcp_servers": [
                            {
                                "ref": "svc",
                                "transport": "streamable-http",
                                "endpoint": "https://mcp.example.test",
                            }
                        ],
                    },
                },
            }
        )
        await manager.connect_server(definition.spec.dependencies.mcp_servers[0])
        tool = manager.tools()["svc:lookup"]
        with invocation_identity(InvocationIdentity(caller=CallerIdentity(caller_id="mcp-caller"))):
            await tool.execute({})
        await manager.aclose()
        assert seen
        assert seen[0] is not None
        assert seen[0].caller is not None
        assert seen[0].caller.caller_id == "mcp-caller"


class TestWorkloadIdentity:
    """Workload identity resolution precedence."""

    def test_env_overrides_win(self, monkeypatch):
        identity = resolve_workload_identity(
            environ={
                "MICRO_AGENT_WORKLOAD_ID": "pod-9",
                "MICRO_AGENT_WORKLOAD_NAMESPACE": "agents",
                "MICRO_AGENT_SERVICE_ACCOUNT": "micro-agent-sa",
            },
            hostname="ignored",
        )
        assert identity.workload_id == "pod-9"
        assert identity.namespace == "agents"
        assert identity.service_account == "micro-agent-sa"

    def test_kubernetes_namespace_file_wins_over_hostname(self, monkeypatch, tmp_path):
        namespace_file = tmp_path / "namespace"
        namespace_file.write_text("agent-ns\n", encoding="utf-8")
        monkeypatch.setattr(
            "micro_agent.security.propagation._K8S_SA_NAMESPACE_PATH", str(namespace_file)
        )
        identity = resolve_workload_identity(environ={}, hostname="pod-abc")
        assert identity.namespace == "agent-ns"
        assert identity.workload_id == "pod-abc"

    def test_fallback_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "micro_agent.security.propagation._K8S_SA_NAMESPACE_PATH",
            str(tmp_path / "missing"),
        )
        identity = resolve_workload_identity(environ={}, hostname="host-1")
        assert identity.workload_id == "host-1"
        assert identity.namespace == "default"
        assert identity.service_account is None
