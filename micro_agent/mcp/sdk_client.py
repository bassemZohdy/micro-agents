"""Official MCP Python SDK wire client.

Implements the :class:`~micro_agent.mcp.mcp.McpClient` SPI with the official
``mcp`` package against the stable ``2025-11-25`` specification. Transports:

- ``streamable-http`` — the standard HTTP transport;
- ``stdio`` — a local server subprocess, modeled by command/args;
- ``sse`` — legacy compatibility only, explicitly not a peer stable
  transport; deployments should migrate to Streamable HTTP.

The SDK session performs initialization and version/capability negotiation.
Each connection runs in a dedicated task holding the transport context
managers; per-call timeouts bound requests; disconnect closes the session
and transports gracefully. Unexpected transport termination triggers bounded
automatic reconnect attempts, while an explicit disconnect never reconnects.
Credentials are injected at connect time — HTTP servers receive an
Authorization header, stdio servers receive the value in the child-process
environment under the credential reference name — and never appear on config
objects, logs, or errors.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, TypeVar

import httpx

from micro_agent.mcp.mcp import (
    McpClient,
    McpConfig,
    McpConnectionState,
    McpDiscovery,
    McpPrompt,
    McpResource,
    McpTool,
)

_CONNECT_TIMEOUT_SECONDS = 10.0
_DISCONNECT_TIMEOUT_SECONDS = 10.0
_CALL_TIMEOUT_SECONDS = 60.0
_RECONNECT_ATTEMPTS = 3
_RECONNECT_BACKOFF_SECONDS = 0.25

_T = TypeVar("_T")


def _direct_httpx_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Build an MCP HTTP client without ambient proxy environment variables."""
    kwargs: dict[str, Any] = {"follow_redirects": True, "trust_env": False}
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


class SdkMcpError(ConnectionError):
    """Raised when the SDK client cannot connect, negotiate, or call."""


class McpToolError(Exception):
    """Raised when a server reports a tool execution error."""


def _import_sdk() -> SimpleNamespace:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client import sse, stdio, streamable_http
    except ImportError as exc:  # pragma: no cover - exercised without extra
        raise SdkMcpError(
            "the official MCP SDK is required; install the optional 'mcp' "
            "extra ('micro-agents[mcp]')"
        ) from exc
    return SimpleNamespace(
        ClientSession=ClientSession,
        StdioServerParameters=StdioServerParameters,
        sse=sse,
        stdio=stdio,
        streamable_http=streamable_http,
    )


class SdkMcpClient(McpClient):
    """Wire-protocol client backed by the official MCP SDK.

    One instance serves one server connection; the connection manager
    constructs a fresh instance per connect through the factory.
    """

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = _CONNECT_TIMEOUT_SECONDS,
        disconnect_timeout_seconds: float = _DISCONNECT_TIMEOUT_SECONDS,
        call_timeout_seconds: float = _CALL_TIMEOUT_SECONDS,
        reconnect_attempts: int = _RECONNECT_ATTEMPTS,
        reconnect_backoff_seconds: float = _RECONNECT_BACKOFF_SECONDS,
    ) -> None:
        if reconnect_attempts < 0:
            raise ValueError("reconnect_attempts must be non-negative")
        if reconnect_backoff_seconds < 0:
            raise ValueError("reconnect_backoff_seconds must be non-negative")
        self._connect_timeout = connect_timeout_seconds
        self._disconnect_timeout = disconnect_timeout_seconds
        self._call_timeout = call_timeout_seconds
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_backoff = reconnect_backoff_seconds
        self._session: Any = None
        self._protocol_version: str | None = None
        self._server_name: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._error: Exception | None = None
        self._state = McpConnectionState.DISCONNECTED
        self._config: McpConfig | None = None
        self._credential: str | None = None
        self._connected_once = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, config: McpConfig, credential: str | None = None) -> None:
        if self._task is not None:
            raise SdkMcpError(f"mcp '{config.ref}': client is already connected")
        self._config = config
        self._credential = credential
        self._state = McpConnectionState.CONNECTING
        self._closing.clear()
        self._ready.clear()
        self._error = None
        self._connected_once = False
        self._task = asyncio.create_task(self._run(), name=f"micro-agent-mcp-{config.ref}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._connect_timeout)
        except TimeoutError as exc:
            self._state = McpConnectionState.ERROR
            await self._abort()
            raise SdkMcpError(
                f"mcp '{config.ref}': connection timed out after {self._connect_timeout}s"
            ) from exc
        if not self._connected_once:
            error = self._error
            self._state = McpConnectionState.ERROR
            self._task = None
            message = f"mcp '{config.ref}': connection failed"
            if error is not None:
                message += f": {error}"
            raise SdkMcpError(message) from error

    async def _run(self) -> None:
        assert self._config is not None
        config = self._config
        try:
            sdk = _import_sdk()
        except Exception as exc:  # noqa: BLE001 — optional SDK/import failures
            self._error = exc
            self._state = McpConnectionState.ERROR
            self._ready.set()
            return

        reconnects = 0
        while not self._closing.is_set():
            try:
                async with AsyncExitStack() as stack:
                    read, write = await self._enter_transport(stack, config, sdk)
                    session = await stack.enter_async_context(sdk.ClientSession(read, write))
                    init = await asyncio.wait_for(
                        session.initialize(), timeout=self._connect_timeout
                    )
                    self._protocol_version = init.protocolVersion
                    self._server_name = getattr(init.serverInfo, "name", None)
                    self._session = session
                    self._connected_once = True
                    self._error = None
                    self._state = McpConnectionState.CONNECTED
                    self._ready.set()
                    reconnects = 0
                    # Hold the transport context open until disconnect. A
                    # transport exception exits this block and is retried.
                    await self._closing.wait()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reconnectable transport failures
                self._session = None
                self._error = exc
                if not self._connected_once or self._closing.is_set():
                    self._state = McpConnectionState.ERROR
                    self._ready.set()
                    return
                reconnects += 1
                self._state = McpConnectionState.CONNECTING
                if reconnects > self._reconnect_attempts:
                    self._state = McpConnectionState.ERROR
                    self._ready.set()
                    return
                self._ready.set()
                delay = self._reconnect_backoff * (2 ** (reconnects - 1))
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._closing.wait(), timeout=delay)
            finally:
                self._session = None

    async def _enter_transport(
        self,
        stack: AsyncExitStack,
        config: McpConfig,
        sdk: SimpleNamespace,
    ) -> tuple[Any, Any]:
        headers = {"Authorization": f"Bearer {self._credential}"} if self._credential else None
        if config.transport == "stdio":
            assert config.command is not None
            env = dict(os.environ)
            if self._credential and config.credential_ref:
                # The reference names the environment variable the server expects.
                env[config.credential_ref] = self._credential
            server = sdk.StdioServerParameters(
                command=config.command, args=list(config.args), env=env
            )
            read, write = await stack.enter_async_context(sdk.stdio.stdio_client(server))
            return read, write
        if config.transport == "sse":
            # Legacy compatibility transport, not a stable peer.
            assert config.endpoint is not None
            return await stack.enter_async_context(
                sdk.sse.sse_client(config.endpoint, headers=headers)
            )
        assert config.endpoint is not None
        timeout = float(config.timeout_seconds) if config.timeout_seconds else 30.0
        read, write, _session_id = await stack.enter_async_context(
            sdk.streamable_http.streamablehttp_client(
                config.endpoint,
                headers=headers,
                timeout=timeout,
                httpx_client_factory=_direct_httpx_client_factory,
            )
        )
        return read, write

    async def disconnect(self) -> None:
        if self._task is None:
            self._state = McpConnectionState.DISCONNECTED
            return
        self._closing.set()
        try:
            await asyncio.wait_for(self._task, timeout=self._disconnect_timeout)
        except TimeoutError:
            self._task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._task
        finally:
            self._task = None
            self._session = None
            self._state = McpConnectionState.DISCONNECTED

    async def _abort(self) -> None:
        if self._task is not None:
            self._closing.set()
            self._task.cancel()
            with suppress(Exception, asyncio.CancelledError):
                await self._task
            self._task = None
        self._session = None

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    async def discover(self) -> McpDiscovery:
        session = self._require_session()
        tools_result = await self._bounded(session.list_tools())
        tools = [
            McpTool(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.inputSchema or {}),
            )
            for tool in tools_result.tools
        ]
        resources = await self._discover_optional(session.list_resources, "resources", _resource)
        prompts = await self._discover_optional(session.list_prompts, "prompts", _prompt)
        return McpDiscovery(tools=tools, resources=resources, prompts=prompts)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session()
        read_timeout = (
            timedelta(seconds=float(self._config.timeout_seconds))
            if self._config is not None and self._config.timeout_seconds
            else None
        )
        try:
            result = await self._bounded(session.call_tool(name, arguments, read_timeout))
        except TimeoutError as exc:
            raise SdkMcpError(f"tool '{name}' timed out") from exc
        if result.isError:
            raise McpToolError(
                _blocks_text(result.content) or f"tool '{name}' failed on the server"
            )
        if getattr(result, "structuredContent", None) is not None:
            return {"result": result.structuredContent}
        return {"content": _content_blocks(result.content)}

    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def protocol_version(self) -> str | None:
        """The protocol version negotiated at initialization."""
        return self._protocol_version

    @property
    def server_name(self) -> str | None:
        """The server name reported during initialization."""
        return self._server_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _bounded(self, operation: Awaitable[_T]) -> _T:
        return await asyncio.wait_for(operation, timeout=self._call_timeout)

    def _require_session(self) -> Any:
        if self._session is None or self._state != McpConnectionState.CONNECTED:
            raise ConnectionError("mcp client is not connected")
        return self._session

    async def _discover_optional(
        self,
        listing: Callable[[], Awaitable[Any]],
        attribute: str,
        adapt: Callable[[Any], Any],
    ) -> list[Any]:
        """List an optional capability; servers without it yield an empty list."""
        try:
            result = await self._bounded(listing())
        except Exception:  # noqa: BLE001 — servers may not expose this capability
            return []
        return [adapt(item) for item in getattr(result, attribute, []) or []]


def _resource(resource: Any) -> McpResource:
    return McpResource(
        uri=str(resource.uri),
        name=resource.name,
        description=resource.description,
        mime_type=resource.mimeType,
    )


def _prompt(prompt: Any) -> McpPrompt:
    return McpPrompt(
        name=prompt.name,
        description=prompt.description,
        arguments=[
            {
                "name": argument.name,
                "description": argument.description,
                "required": argument.required,
            }
            for argument in (prompt.arguments or [])
        ],
    )


def _blocks_text(blocks: Any) -> str:
    texts = [
        block.text
        for block in (blocks or [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    ]
    return "\n".join(texts)


def _content_blocks(blocks: Any) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for block in blocks or []:
        block_type = getattr(block, "type", "unknown")
        if block_type == "text":
            mapped.append({"type": "text", "text": block.text})
        else:
            mapped.append({"type": block_type})
    return mapped


def sdk_available() -> bool:
    """Whether the official MCP SDK is importable."""
    try:
        _import_sdk()
    except SdkMcpError:
        return False
    return True


def sdk_client_factory() -> Callable[[McpConfig], McpClient]:
    """Build a connection-manager client factory backed by the official SDK.

    Raises :class:`SdkMcpError` at bootstrap when the optional extra is
    missing, instead of failing per connect.
    """
    _import_sdk()

    def factory(config: McpConfig) -> McpClient:
        return SdkMcpClient(
            connect_timeout_seconds=(
                float(config.timeout_seconds)
                if config.transport == "stdio" and config.timeout_seconds
                else _CONNECT_TIMEOUT_SECONDS
            ),
            call_timeout_seconds=(
                float(config.timeout_seconds) if config.timeout_seconds else _CALL_TIMEOUT_SECONDS
            ),
        )

    return factory


__all__ = ["McpToolError", "SdkMcpClient", "SdkMcpError", "sdk_available", "sdk_client_factory"]
