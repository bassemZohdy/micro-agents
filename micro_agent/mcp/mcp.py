"""Micro-Agent MCP (Model Context Protocol) integration.

MCP is a first-class Micro-Agent dependency.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# MCP Configuration
# ---------------------------------------------------------------------------


@dataclass
class McpConfig:
    """MCP server configuration from definition."""

    ref: str
    transport: str | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    allowed_capabilities: list[str] = field(default_factory=list)
    timeout_seconds: int | None = None


# ---------------------------------------------------------------------------
# MCP Discovered Resources
# ---------------------------------------------------------------------------


@dataclass
class McpTool:
    """A tool discovered from an MCP server."""

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResource:
    """A resource discovered from an MCP server."""

    uri: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None


@dataclass
class McpPrompt:
    """A prompt discovered from an MCP server."""

    name: str
    description: str | None = None
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class McpDiscovery:
    """Results of MCP server discovery."""

    tools: list[McpTool] = field(default_factory=list)
    resources: list[McpResource] = field(default_factory=list)
    prompts: list[McpPrompt] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MCP Connection State
# ---------------------------------------------------------------------------


class McpConnectionState:
    """MCP connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


# ---------------------------------------------------------------------------
# MCP Client Interface
# ---------------------------------------------------------------------------


class McpClient(ABC):
    """Abstract MCP client interface."""

    @abstractmethod
    async def connect(self, config: McpConfig) -> None:
        """Connect to an MCP server."""

    @abstractmethod
    async def discover(self) -> McpDiscovery:
        """Discover tools, resources, and prompts."""

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the MCP server gracefully."""

    @abstractmethod
    def state(self) -> str:
        """Return current connection state."""
