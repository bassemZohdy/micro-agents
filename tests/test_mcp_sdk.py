"""Official MCP SDK interop tests against real MCP servers.

A FastMCP server is exercised over stdio (a real subprocess) and over
Streamable HTTP (uvicorn on loopback), proving initialization, version
negotiation, discovery, tool invocation, error mapping, timeouts, and
graceful close through :class:`SdkMcpClient`.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

from micro_agent.mcp.mcp import McpConfig, McpConnectionState
from micro_agent.mcp.sdk_client import McpToolError, SdkMcpClient

pytest.importorskip("mcp")

_SERVER_SCRIPT = textwrap.dedent(
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("interop-server")


    @mcp.tool()
    def echo(message: str) -> str:
        \"\"\"Echo the message back.\"\"\"
        return message


    @mcp.tool()
    def add(left: int, right: int) -> int:
        \"\"\"Add two integers.\"\"\"
        return left + right


    @mcp.tool()
    def secret_check() -> str:
        \"\"\"Report whether TEST_TOKEN was injected.\"\"\"
        import os

        return "present" if os.environ.get("TEST_TOKEN") else "missing"


    @mcp.tool()
    def slow(seconds: int = 30) -> str:
        \"\"\"Sleep before answering.\"\"\"
        import time

        time.sleep(seconds)
        return "done"


    @mcp.tool()
    def fail() -> str:
        \"\"\"Always raises.\"\"\"
        raise RuntimeError("boom")


    @mcp.resource("config://greeting")
    def greeting() -> str:
        return "hello from the server"


    @mcp.prompt()
    def greet_prompt(name: str) -> str:
        return f"Greet {name} warmly."


    mcp.run(transport="stdio")
    """
)


def _stdio_config(tmp_path, *, timeout_seconds: int | None = 10) -> McpConfig:
    script = tmp_path / "mcp_server.py"
    script.write_text(_SERVER_SCRIPT, encoding="utf-8")
    return McpConfig(
        ref="interop",
        transport="stdio",
        command=sys.executable,
        args=[str(script)],
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_stdio_interop_discover_call_and_close(tmp_path):
    client = SdkMcpClient()
    config = _stdio_config(tmp_path)
    await client.connect(config)
    try:
        assert client.state() == McpConnectionState.CONNECTED
        # Version/capability negotiation happened at initialize().
        assert client.protocol_version
        assert client.server_name == "interop-server"

        discovery = await client.discover()
        tool_names = {tool.name for tool in discovery.tools}
        assert {"echo", "add", "secret_check", "slow", "fail"} <= tool_names
        echo_tool = next(tool for tool in discovery.tools if tool.name == "echo")
        assert "message" in echo_tool.input_schema.get("properties", {})
        assert any(resource.uri == "config://greeting" for resource in discovery.resources)
        assert any(prompt.name == "greet_prompt" for prompt in discovery.prompts)

        payload = await client.call_tool("add", {"left": 2, "right": 3})
        # FastMCP wraps scalar returns under a "result" key in structured output.
        assert payload == {"result": {"result": 5}}

        text_payload = await client.call_tool("echo", {"message": "wire"})
        assert text_payload == {"result": {"result": "wire"}}
    finally:
        await client.disconnect()
    assert client.state() == McpConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_stdio_credential_reaches_server_environment(tmp_path):

    client = SdkMcpClient()
    config = _stdio_config(tmp_path)
    config.credential_ref = "TEST_TOKEN"
    await client.connect(config, credential="secret-value")
    try:
        payload = await client.call_tool("secret_check", {})
        assert payload == {"result": {"result": "present"}}
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_server_tool_error_maps_to_tool_error(tmp_path):
    client = SdkMcpClient()
    await client.connect(_stdio_config(tmp_path))
    try:
        with pytest.raises(McpToolError, match="boom"):
            await client.call_tool("fail", {})
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_per_call_timeout_bounds_slow_tools(tmp_path):
    client = SdkMcpClient(call_timeout_seconds=1.0)
    await client.connect(_stdio_config(tmp_path))
    try:
        from micro_agent.mcp.sdk_client import SdkMcpError

        with pytest.raises(SdkMcpError, match="timed out"):
            await client.call_tool("slow", {"seconds": 5})
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_connect_failure_propagates_clear_error():
    client = SdkMcpClient(connect_timeout_seconds=5.0)
    config = McpConfig(
        ref="missing",
        transport="stdio",
        command=sys.executable,
        args=["-c", "raise SystemExit(1)"],
    )
    with pytest.raises(Exception, match="connection failed"):
        await client.connect(config)


@pytest.mark.asyncio
async def test_unexpected_transport_drop_reconnects(monkeypatch):
    """A dropped transport is retried without requiring a runtime restart."""

    class DropOnceEvent:
        def __init__(self) -> None:
            self._event = asyncio.Event()
            self._drop = True

        def clear(self) -> None:
            # ``connect`` clears the event before starting the transport, but
            # the first wait is deliberately used to model a dropped link.
            self._event.clear()

        def is_set(self) -> bool:
            return self._event.is_set()

        def set(self) -> None:
            self._event.set()

        async def wait(self) -> None:
            if self._drop:
                self._drop = False
                raise ConnectionError("simulated transport drop")
            await self._event.wait()

    class Session:
        def __init__(self, _read, _write) -> None:
            self.protocolVersion = "2025-11-25"
            self.serverInfo = SimpleNamespace(name="reconnect-server")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def initialize(self):
            return self

    client = SdkMcpClient(
        reconnect_attempts=2,
        reconnect_backoff_seconds=0.001,
    )
    closing = DropOnceEvent()
    client._closing = closing

    async def enter_transport(_stack, _config, _sdk):
        return object(), object()

    monkeypatch.setattr(
        "micro_agent.mcp.sdk_client._import_sdk",
        lambda: SimpleNamespace(ClientSession=Session),
    )
    monkeypatch.setattr(client, "_enter_transport", enter_transport)

    try:
        await client.connect(McpConfig(ref="reconnect"))
        for _ in range(100):
            if client.state() == McpConnectionState.CONNECTED and client._connected_once:
                break
            await asyncio.sleep(0.001)
        assert client.state() == McpConnectionState.CONNECTED
        assert client.protocol_version == "2025-11-25"
    finally:
        await client.disconnect()


def test_reconnect_arguments_are_validated():
    with pytest.raises(ValueError, match="reconnect_attempts"):
        SdkMcpClient(reconnect_attempts=-1)
    with pytest.raises(ValueError, match="reconnect_backoff_seconds"):
        SdkMcpClient(reconnect_backoff_seconds=-0.1)


class _UvicornThread:
    """Serves the FastMCP Streamable HTTP app on a loopback ephemeral port."""

    def __init__(self, app) -> None:
        import uvicorn

        self._server = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        self._uvicorn = uvicorn.Server(self._server)
        self._thread = threading.Thread(target=self._uvicorn.run, daemon=True)

    @property
    def port(self) -> int:
        for _ in range(100):
            if self._uvicorn.started:
                for socket in self._uvicorn.servers[0].sockets:
                    return int(socket.getsockname()[1])
            time.sleep(0.05)
        raise RuntimeError("uvicorn did not start")

    def __enter__(self) -> _UvicornThread:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._uvicorn.should_exit = True
        self._thread.join(timeout=10)


def _http_app():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("http-interop")

    @mcp.tool()
    def echo(message: str) -> str:
        """Echo the message back."""
        return message

    return mcp.streamable_http_app()


@pytest.mark.asyncio
async def test_streamable_http_interop_discover_and_call():
    with _UvicornThread(_http_app()) as server:
        port = server.port
        client = SdkMcpClient()
        config = McpConfig(
            ref="http-interop",
            transport="streamable-http",
            endpoint=f"http://127.0.0.1:{port}/mcp",
        )
        await client.connect(config)
        try:
            assert client.protocol_version
            discovery = await client.discover()
            assert "echo" in {tool.name for tool in discovery.tools}
            payload = await client.call_tool("echo", {"message": "over-http"})
            assert payload == {"result": {"result": "over-http"}}
        finally:
            await client.disconnect()


@pytest.mark.asyncio
async def test_yaml_only_declaration_invokes_real_server_through_bootstrap(tmp_path):
    """P1.2 acceptance: YAML-only MCP declaration works through the bootstrap."""
    from micro_agent.config import build_runtime
    from micro_agent.core import AgentRequest, DefaultMicroAgent
    from micro_agent.definition import load_definition_from_dict
    from micro_agent.models import FakeModelConfig, FakeModelProvider
    from runtimes.adk import AdkRuntime, AdkRuntimeConfig

    script = tmp_path / "mcp_server.py"
    script.write_text(_SERVER_SCRIPT, encoding="utf-8")
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "mcp-acceptance", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Use MCP tools."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "interop",
                            "transport": "stdio",
                            "command": sys.executable,
                            "args": [str(script)],
                            "timeout_seconds": 10,
                        }
                    ],
                },
            },
        }
    )

    class OneShotProvider(FakeModelProvider):
        async def generate(self, config, messages, tools=None):
            if len(self.invocations) >= 1:
                self._config.tool_requests = []
            else:
                self._config.tool_requests = [
                    {"name": "interop:add", "arguments": {"left": 20, "right": 22}}
                ]
            return await super().generate(config, messages, tools=tools)

    bootstrap = build_runtime(definition)
    runtime = AdkRuntime(
        AdkRuntimeConfig(
            model_provider=OneShotProvider(FakeModelConfig(response="computed")),
            mcp_manager=bootstrap.runtime._config.mcp_manager,
        )
    )
    agent = DefaultMicroAgent(definition, runtime)
    try:
        await agent.initialize()
        # Startup connects and discovers through the real SDK client.
        await agent.start()
        response = await agent.invoke(AgentRequest(input={}))
        tool_results = response.output["tool_results"]
        assert tool_results, "the MCP tool must have been invoked"
        assert tool_results[0]["output"] == {"result": {"result": 42}}
        assert response.metadata["tools_called"] == ["interop:add"]
    finally:
        await agent.stop()
        await agent.shutdown()
        await runtime.close()
