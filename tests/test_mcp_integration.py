"""MCP integration: fake client, security, MCP-by-configuration acceptance."""

import pytest

from micro_agent.core import AgentRequest
from micro_agent.definition import load_definition_from_dict
from micro_agent.mcp import (
    FakeMcpClient,
    McpConnectionManager,
    McpSecurityError,
    McpSecurityPolicy,
)
from micro_agent.mcp.mcp import McpConfig, McpPrompt, McpResource, McpTool
from micro_agent.models import FakeModelConfig, FakeModelProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

pytestmark = pytest.mark.integration


def _definition_with_mcp() -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "mcp-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Use the MCP tools."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "mcp_servers": [
                        {
                            "ref": "residency-services",
                            "transport": "streamable-http",
                            "endpoint": "https://mcp.example.com",
                        }
                    ],
                },
            },
        }
    )


def _fake_client(**overrides) -> FakeMcpClient:
    return FakeMcpClient(
        tools=[McpTool(name="check_status", description="Check renewal status.")],
        resources=[McpResource(uri="mcp://rules/2024", name="Rules 2024")],
        prompts=[McpPrompt(name="status-prompt")],
        handlers={"check_status": lambda args: {"status": "submitted", "args": args}},
        **overrides,
    )


class TestMcpSecurity:
    """Security controls: TLS, endpoint validation, transport check."""

    def test_http_endpoint_rejected_without_local_exception(self):
        policy = McpSecurityPolicy(require_tls=True, allow_insecure_localhost=False)
        config = McpConfig(
            ref="svc", transport="streamable-http", endpoint="http://mcp.example.com"
        )
        with pytest.raises(McpSecurityError, match="TLS required"):
            policy.validate(config)

    def test_http_localhost_allowed(self):
        policy = McpSecurityPolicy()
        config = McpConfig(ref="svc", transport="streamable-http", endpoint="http://localhost:9000")
        policy.validate(config)

    def test_https_endpoint_accepted(self):
        policy = McpSecurityPolicy()
        config = McpConfig(
            ref="svc", transport="streamable-http", endpoint="https://mcp.example.com"
        )
        policy.validate(config)

    def test_endpoint_allowlist(self):
        policy = McpSecurityPolicy(allowed_endpoints=["https://mcp.example.com"])
        policy.validate(McpConfig(ref="ok", endpoint="https://mcp.example.com", transport="sse"))
        with pytest.raises(McpSecurityError, match="allowlist"):
            policy.validate(
                McpConfig(ref="bad", endpoint="https://evil.example.com", transport="sse")
            )

    def test_unknown_transport_rejected(self):
        policy = McpSecurityPolicy()
        with pytest.raises(McpSecurityError, match="transport"):
            policy.validate(
                McpConfig(ref="svc", transport="carrier-pigeon", endpoint="https://x.example.com")
            )

    def test_missing_endpoint_rejected(self):
        policy = McpSecurityPolicy()
        with pytest.raises(McpSecurityError, match="endpoint"):
            policy.validate(McpConfig(ref="svc", transport="streamable-http"))


class TestFakeMcpClient:
    """Fake MCP client lifecycle and discovery."""

    @pytest.mark.asyncio
    async def test_connect_discover_call_disconnect(self):
        client = _fake_client()
        assert client.state() == "disconnected"
        config = McpConfig(ref="svc", endpoint="https://mcp.example.com")
        await client.connect(config)
        assert client.state() == "connected"
        discovery = await client.discover()
        assert [t.name for t in discovery.tools] == ["check_status"]
        assert discovery.resources[0].uri == "mcp://rules/2024"
        assert discovery.prompts[0].name == "status-prompt"
        result = await client.call_tool("check_status", {"id": "42"})
        assert result["status"] == "submitted"
        await client.disconnect()
        assert client.state() == "disconnected"

    @pytest.mark.asyncio
    async def test_connect_failure_sets_error_state(self):
        client = _fake_client(connect_fails=True)
        with pytest.raises(ConnectionError):
            await client.connect(McpConfig(ref="svc", endpoint="https://mcp.example.com"))
        assert client.state() == "error"


class TestMcpConnectionManager:
    """Connection manager wiring a definition to MCP clients."""

    @pytest.mark.asyncio
    async def test_connect_and_tool_adapters(self):
        clients = {"residency-services": _fake_client()}
        manager = McpConnectionManager(
            client_factory=lambda config: clients[config.ref],
        )
        definition = _definition_with_mcp()
        await manager.connect_definition(definition)
        tools = manager.tools()
        assert list(tools) == ["residency-services:check_status"]
        adapter = tools["residency-services:check_status"]
        assert adapter.metadata.source == "mcp"
        result = await adapter.execute({"id": "7"})
        assert result.output == {"status": "submitted", "args": {"id": "7"}}
        # Resources/prompts metadata preserved
        discovery = manager.discovery("residency-services")
        assert discovery is not None
        assert discovery.resources[0].uri == "mcp://rules/2024"
        assert await manager.health_probe() is True
        await manager.aclose()
        assert manager.tools() == {}
        assert await manager.health_probe() is False

    @pytest.mark.asyncio
    async def test_security_violation_blocks_connection(self):
        manager = McpConnectionManager(
            security_policy=McpSecurityPolicy(allowed_endpoints=["https://other.example.com"]),
            client_factory=lambda config: _fake_client(),
        )
        with pytest.raises(McpSecurityError, match="allowlist"):
            await manager.connect_definition(_definition_with_mcp())

    @pytest.mark.asyncio
    async def test_connection_failure_propagates(self):
        manager = McpConnectionManager(
            client_factory=lambda config: _fake_client(connect_fails=True),
        )
        with pytest.raises(ConnectionError):
            await manager.connect_definition(_definition_with_mcp())

    @pytest.mark.asyncio
    async def test_response_size_limit(self):
        clients = {
            "residency-services": FakeMcpClient(
                tools=[McpTool(name="big_tool")],
                handlers={"big_tool": lambda args: {"blob": "x" * 2_000_000}},
            )
        }
        manager = McpConnectionManager(
            security_policy=McpSecurityPolicy(max_response_bytes=1024),
            client_factory=lambda config: clients[config.ref],
        )
        await manager.connect_definition(_definition_with_mcp())
        adapter = manager.tools()["residency-services:big_tool"]
        result = await adapter.execute({})
        assert result.is_error
        assert "too large" in (result.error or "")


class OneShotToolProvider(FakeModelProvider):
    """Returns a tool request on the first call only."""

    async def generate(self, config, messages, tools=None):
        if len(self.invocations) == 1:
            self._config.tool_requests = []
        return await super().generate(config, messages, tools=tools)


class TestMcpByConfiguration:
    """Acceptance: MCP attached through configuration only."""

    @pytest.mark.asyncio
    async def test_mcp_tool_used_in_invoke_path(self):
        clients = {"residency-services": _fake_client()}
        manager = McpConnectionManager(
            client_factory=lambda config: clients[config.ref],
        )
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=OneShotToolProvider(
                    FakeModelConfig(
                        response="status checked",
                        tool_requests=[
                            {
                                "name": "residency-services:check_status",
                                "arguments": {"id": "9"},
                            }
                        ],
                    )
                ),
                mcp_manager=manager,
            )
        )
        assert runtime.capabilities().mcp is True
        agent = await runtime.create(_definition_with_mcp())
        await runtime.start(agent)
        response = await runtime.invoke(agent, AgentRequest(input={"id": "9"}))
        assert response.status == "success"
        assert response.metadata["tools_called"] == ["residency-services:check_status"]
        assert response.output["tool_results"][0]["output"]["status"] == "submitted"
        await runtime.close()
