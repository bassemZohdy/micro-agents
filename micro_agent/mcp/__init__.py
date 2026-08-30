"""Micro-Agent MCP — Model Context Protocol integration."""

from micro_agent.mcp.client import (
    FakeMcpClient,
    McpConnectionManager,
    McpSecurityError,
    McpSecurityPolicy,
    McpToolAdapter,
    config_from_definition,
)
from micro_agent.mcp.mcp import (
    McpClient,
    McpConfig,
    McpConnectionState,
    McpDiscovery,
    McpPrompt,
    McpResource,
    McpTool,
)

__all__ = [
    "FakeMcpClient",
    "McpClient",
    "McpConfig",
    "McpConnectionManager",
    "McpConnectionState",
    "McpDiscovery",
    "McpPrompt",
    "McpResource",
    "McpSecurityError",
    "McpSecurityPolicy",
    "McpTool",
    "McpToolAdapter",
    "config_from_definition",
]
