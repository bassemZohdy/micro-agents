"""Concrete MCP client pieces: fake client, security, adapter, connection manager.

A production network client (official MCP SDK) can be supplied later via the
connection manager's client_factory; everything else in this module is
transport-agnostic and exercised with the fake client in tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from micro_agent.definition import McpServerRef
from micro_agent.mcp.mcp import (
    McpClient,
    McpConfig,
    McpConnectionState,
    McpDiscovery,
    McpPrompt,
    McpResource,
    McpTool,
)
from micro_agent.tools import Tool, ToolInputSchema, ToolMetadata, ToolOutputSchema, ToolResult

_SUPPORTED_TRANSPORTS = {"streamable-http", "sse", "stdio"}
_INSECURE_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class McpSecurityError(Exception):
    """Raised when an MCP server configuration fails security validation."""


@dataclass
class McpSecurityPolicy:
    """Security controls applied to MCP server connections.

    - TLS validation: http:// endpoints are rejected unless they are local.
    - Endpoint validation: optional origin allowlist and transport check.
    - Response limits: oversized MCP responses are rejected by the adapter.
    - Credential redaction: credentials never appear in metadata, errors, or
      logs; only resolved inside the client factory.
    """

    require_tls: bool = True
    allow_insecure_localhost: bool = True
    allowed_endpoints: list[str] | None = None
    max_response_bytes: int = 1_048_576

    def validate(self, config: McpConfig) -> None:
        """Validate a server config; raises McpSecurityError on violation.

        stdio servers are local commands validated by the command presence;
        HTTP-based servers are validated by endpoint scheme (TLS required
        outside localhost) and the optional origin allowlist.
        """
        if config.transport is not None and config.transport not in _SUPPORTED_TRANSPORTS:
            raise McpSecurityError(
                f"mcp '{config.ref}': unsupported transport '{config.transport}'"
            )
        if config.transport == "stdio" or (config.transport is None and config.endpoint is None):
            if not config.command:
                raise McpSecurityError(f"mcp '{config.ref}': stdio command is required")
            return
        if not config.endpoint:
            raise McpSecurityError(f"mcp '{config.ref}': endpoint is required")
        parts = urlsplit(config.endpoint)
        if parts.scheme not in ("http", "https"):
            raise McpSecurityError(
                f"mcp '{config.ref}': endpoint scheme must be http(s), got '{parts.scheme}'"
            )
        if self.allowed_endpoints is not None:
            origin = f"{parts.scheme}://{parts.netloc}"
            if not any(
                origin == a or origin.startswith(a.rstrip("/") + "/")
                for a in self.allowed_endpoints
            ):
                raise McpSecurityError(
                    f"mcp '{config.ref}': endpoint '{origin}' is not in the allowlist"
                )
        if self.require_tls and parts.scheme != "https":
            is_local = (parts.hostname or "").lower() in _INSECURE_LOCAL_HOSTS
            if not (self.allow_insecure_localhost and is_local):
                raise McpSecurityError(
                    f"mcp '{config.ref}': TLS required; 'https' endpoint expected"
                )


# ---------------------------------------------------------------------------
# Fake client (test double)
# ---------------------------------------------------------------------------


class FakeMcpClient(McpClient):
    """In-memory MCP client for testing.

    Configured with discovered capabilities and tool handlers; can be told to
    fail connect/call to exercise failure paths.
    """

    def __init__(
        self,
        tools: list[McpTool] | None = None,
        resources: list[McpResource] | None = None,
        prompts: list[McpPrompt] | None = None,
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        connect_fails: bool = False,
    ) -> None:
        self._tools = tools or []
        self._resources = resources or []
        self._prompts = prompts or []
        self._handlers = handlers or {}
        self._connect_fails = connect_fails
        self._state = McpConnectionState.DISCONNECTED
        self._config: McpConfig | None = None
        self._credential: str | None = None

    async def connect(self, config: McpConfig, credential: str | None = None) -> None:
        if self._connect_fails:
            self._state = McpConnectionState.ERROR
            raise ConnectionError(f"cannot reach mcp server '{config.ref}'")
        self._config = config
        self._credential = credential
        self._state = McpConnectionState.CONNECTED

    async def discover(self) -> McpDiscovery:
        return McpDiscovery(
            tools=list(self._tools),
            resources=list(self._resources),
            prompts=list(self._prompts),
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._state != McpConnectionState.CONNECTED:
            raise ConnectionError("mcp client is not connected")
        handler = self._handlers.get(name)
        if handler is None:
            raise KeyError(f"unknown tool '{name}'")
        return handler(arguments)

    async def disconnect(self) -> None:
        self._state = McpConnectionState.DISCONNECTED

    def state(self) -> str:
        return self._state


# ---------------------------------------------------------------------------
# Tool adapter: exposes an MCP tool through the runtime Tool contract
# ---------------------------------------------------------------------------


class McpToolAdapter(Tool):
    """A discovered MCP tool exposed to the runtime as a Tool."""

    def __init__(
        self,
        server_ref: str,
        mcp_tool: McpTool,
        client: McpClient,
        max_response_bytes: int,
        timeout_seconds: int | None = None,
    ) -> None:
        self._key = f"{server_ref}:{mcp_tool.name}"
        self._mcp_tool = mcp_tool
        self._client = client
        self._max_response_bytes = max_response_bytes
        self._timeout = timeout_seconds

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name=self._key,
            description=self._mcp_tool.description
            or f"MCP tool '{self._mcp_tool.name}' from '{self._key.split(':', 1)[0]}'",
            source="mcp",
            timeout_seconds=self._timeout,
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(parameters=self._mcp_tool.input_schema)

    @property
    def output_schema(self) -> ToolOutputSchema:
        return ToolOutputSchema()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            result = await self._client.call_tool(self._mcp_tool.name, arguments)
        except Exception as exc:  # noqa: BLE001 — failures become ToolResults
            return ToolResult(output=None, error=str(exc), is_error=True)
        size = len(json.dumps(result, default=str).encode("utf-8"))
        if size > self._max_response_bytes:
            return ToolResult(
                output=None,
                error=f"mcp response too large ({size} bytes > {self._max_response_bytes})",
                is_error=True,
            )
        return ToolResult(output=result)


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


def config_from_definition(ref: McpServerRef, credential: str | None = None) -> McpConfig:
    """Build an McpConfig from a definition's McpServerRef.

    The resolved credential is carried separately from string fields so it can
    never leak into repr/logs of the config itself.
    """
    return McpConfig(
        ref=ref.ref,
        transport=ref.transport,
        endpoint=ref.endpoint,
        command=ref.command,
        args=list(ref.args),
        credential_ref=ref.credential_ref,
        allowed_capabilities=list(ref.allowed_capabilities),
        timeout_seconds=ref.timeout_seconds,
    )


class McpConnectionManager:
    """Connects the MCP servers declared in a definition, by configuration.

    `client_factory` decides how a real connection is made; tests inject
    FakeMcpClient instances. Discovered tools are exposed to the runtime as
    Tools (namespaced `server:tool`); resources/prompts metadata is preserved.
    """

    def __init__(
        self,
        security_policy: McpSecurityPolicy | None = None,
        client_factory: Callable[[McpConfig], McpClient] | None = None,
        credential_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._security = security_policy or McpSecurityPolicy()
        self._client_factory = client_factory
        self._credential_resolver = credential_resolver
        self._clients: dict[str, McpClient] = {}
        self._tools: dict[str, McpToolAdapter] = {}
        self._discovery: dict[str, McpDiscovery] = {}

    def _resolve_credential(self, config: McpConfig) -> str | None:
        """Resolve a declared credential reference through the provider."""
        if not config.credential_ref:
            return None
        if self._credential_resolver is None:
            raise McpSecurityError(
                f"mcp '{config.ref}': credential_ref requires a configured credential provider"
            )
        credential = self._credential_resolver(config.credential_ref)
        if credential is None:
            raise McpSecurityError(
                f"mcp '{config.ref}': credential '{config.credential_ref}' is not available"
            )
        return credential

    def _default_client(self, config: McpConfig) -> McpClient:
        if self._client_factory is None:
            raise McpSecurityError(
                "no MCP client factory configured; install the official SDK "
                "extra ('micro-agents[mcp]') or supply a client factory that "
                f"speaks the MCP wire protocol for server '{config.ref}'"
            )
        return self._client_factory(config)

    async def connect_server(self, ref: McpServerRef) -> None:
        """Validate, connect to, and discover one configured MCP server.

        Declared credentials are resolved through the configured credential
        provider at connect time and are never stored on the config.
        """
        config = config_from_definition(ref)
        self._security.validate(config)
        credential = self._resolve_credential(config)
        client = self._default_client(config)
        await client.connect(config, credential)
        if client.state() != McpConnectionState.CONNECTED:
            raise ConnectionError(f"mcp '{ref.ref}' did not reach connected state")
        discovery = await client.discover()
        for mcp_tool in discovery.tools:
            adapter = McpToolAdapter(
                server_ref=ref.ref,
                mcp_tool=mcp_tool,
                client=client,
                max_response_bytes=self._security.max_response_bytes,
                timeout_seconds=ref.timeout_seconds,
            )
            self._tools[adapter.metadata.name] = adapter
        self._clients[ref.ref] = client
        self._discovery[ref.ref] = discovery

    async def connect_definition(self, definition: Any) -> None:
        """Connect every MCP server declared in a MicroAgentDefinition."""
        for ref in definition.spec.dependencies.mcp_servers:
            await self.connect_server(ref)

    def tools(self) -> dict[str, McpToolAdapter]:
        """Discovered MCP tools, keyed by `server:tool`."""
        return dict(self._tools)

    def discovery(self, server_ref: str) -> McpDiscovery | None:
        """Preserved discovery metadata (resources and prompts) for a server."""
        return self._discovery.get(server_ref)

    async def health_probe(self) -> bool:
        """Healthy when every connected client is still connected."""
        return bool(self._clients) and all(
            client.state() == McpConnectionState.CONNECTED for client in self._clients.values()
        )

    async def aclose(self) -> None:
        """Graceful shutdown of all MCP connections."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
        self._tools.clear()
        self._discovery.clear()
