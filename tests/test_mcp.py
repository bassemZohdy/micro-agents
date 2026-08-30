"""Tests for Micro-Agent MCP integration."""

import pytest

from micro_agent.mcp import (
    McpClient,
    McpConfig,
    McpConnectionState,
    McpDiscovery,
    McpPrompt,
    McpResource,
    McpTool,
)


class TestMcpConfig:
    """Test MCP configuration."""

    def test_basic_config(self):
        config = McpConfig(ref="test-mcp")
        assert config.ref == "test-mcp"
        assert config.transport is None
        assert config.endpoint is None

    def test_full_config(self):
        config = McpConfig(
            ref="residency-services",
            transport="streamable-http",
            endpoint="https://mcp.example.com",
            allowed_capabilities=["tools", "resources"],
            timeout_seconds=15,
        )
        assert config.transport == "streamable-http"
        assert len(config.allowed_capabilities) == 2


class TestMcpDiscovery:
    """Test MCP discovery results."""

    def test_empty_discovery(self):
        discovery = McpDiscovery()
        assert discovery.tools == []
        assert discovery.resources == []
        assert discovery.prompts == []

    def test_discovery_with_tools(self):
        tool = McpTool(name="check_status", description="Check status")
        discovery = McpDiscovery(tools=[tool])
        assert len(discovery.tools) == 1
        assert discovery.tools[0].name == "check_status"


class TestMcpTool:
    """Test MCP tool."""

    def test_tool_creation(self):
        tool = McpTool(name="test", description="A test tool")
        assert tool.name == "test"
        assert tool.input_schema == {}


class TestMcpResource:
    """Test MCP resource."""

    def test_resource_creation(self):
        resource = McpResource(uri="file:///data", name="data")
        assert resource.uri == "file:///data"


class TestMcpPrompt:
    """Test MCP prompt."""

    def test_prompt_creation(self):
        prompt = McpPrompt(name="greet", description="Greeting prompt")
        assert prompt.name == "greet"


class TestMcpConnectionState:
    """Test MCP connection states."""

    def test_states(self):
        assert McpConnectionState.DISCONNECTED == "disconnected"
        assert McpConnectionState.CONNECTING == "connecting"
        assert McpConnectionState.CONNECTED == "connected"
        assert McpConnectionState.ERROR == "error"


class TestMcpClientInterface:
    """Test that McpClient is properly abstract."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            McpClient()  # type: ignore[abstract]
