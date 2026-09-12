"""Downstream token exchange and per-request MCP credential tests."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from micro_agent.definition import load_definition_from_dict
from micro_agent.mcp import FakeMcpClient, McpConfig, McpConnectionManager
from micro_agent.mcp.sdk_client import SdkMcpClient
from micro_agent.security import (
    CallerIdentity,
    DelegatedToken,
    HttpTokenExchangeProvider,
    InvocationIdentity,
    RuntimeIdentity,
    TokenExchangeError,
    TokenExchangeProvider,
    UserContext,
    invocation_identity,
)


def _identity() -> InvocationIdentity:
    return InvocationIdentity(
        caller=CallerIdentity("caller-7", metadata={"source": "oidc"}),
        user=UserContext("user-7", tenant_id="tenant-7", roles=["reader"]),
        workload=RuntimeIdentity("pod-7", namespace="agents", service_account="agent"),
    )


@pytest.mark.asyncio
async def test_http_exchange_sends_only_verified_identity_and_actor_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"access_token": "downstream-7", "token_type": "Bearer", "expires_in": 60},
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpTokenExchangeProvider(
        "https://issuer.example.test/token",
        client=transport,
    )
    try:
        with invocation_identity(_identity()):
            token = await provider.exchange(audience="orders", actor_token="agent-token")
    finally:
        await transport.aclose()

    assert token == DelegatedToken("downstream-7", expires_in=60)
    payload = parse_qs(seen[0].content.decode())
    assert payload["audience"] == ["orders"]
    assert payload["actor_token"] == ["agent-token"]
    identity = json.loads(payload["micro_agent_identity"][0])
    assert identity["user"]["tenant_id"] == "tenant-7"
    assert "metadata" not in identity["caller"]
    assert "Authorization" not in seen[0].headers


@pytest.mark.asyncio
async def test_http_exchange_rejects_invalid_response_without_leaking_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "issued", "unexpected": "field"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpTokenExchangeProvider(
        "https://issuer.example.test/token",
        client=transport,
    )
    try:
        with pytest.raises(TokenExchangeError, match="invalid contract"):
            await provider.exchange(audience="orders")
    finally:
        await transport.aclose()


def test_http_exchange_rejects_non_loopback_http() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpTokenExchangeProvider("http://issuer.example.test/token")


class _RecordingExchange(TokenExchangeProvider):
    def __init__(self) -> None:
        self.identities: list[InvocationIdentity | None] = []
        self.closed = False

    async def exchange(self, *, audience, actor_token=None, identity=None):
        self.identities.append(identity)
        return DelegatedToken(f"delegated-{len(self.identities)}")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_mcp_manager_refreshes_delegated_token_for_each_invocation() -> None:
    client = FakeMcpClient()
    exchange = _RecordingExchange()
    manager = McpConnectionManager(
        client_factory=lambda _config: client,
        token_exchange_provider=exchange,
    )
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "delegation", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "delegate"},
                "dependencies": {
                    "model": {"ref": "fake", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "orders",
                            "transport": "streamable-http",
                            "endpoint": "https://mcp.example.test/mcp",
                        }
                    ],
                },
            },
        }
    )
    await manager.connect_server(definition.spec.dependencies.mcp_servers[0])
    assert client._credential == "delegated-1"

    resolver = client._credential  # The fake stores only the initial token.
    assert resolver == "delegated-1"
    with invocation_identity(_identity()):
        refreshed = await manager._resolve_downstream_credential(
            McpConfig(ref="orders", transport="streamable-http", endpoint="https://mcp"),
            None,
        )
    assert refreshed == "delegated-2"
    assert exchange.identities[-1] == _identity()
    await manager.aclose()
    assert exchange.closed


@pytest.mark.asyncio
async def test_sdk_request_hook_overrides_static_credential() -> None:
    client = SdkMcpClient()
    client._request_credentials[7] = "delegated-request"
    transport = client._httpx_client_factory()(headers={"Authorization": "Bearer initial"})
    request = httpx.Request(
        "POST",
        "https://mcp.example.test/mcp",
        content=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call"}),
    )
    await transport.event_hooks["request"][0](request)
    assert request.headers["Authorization"] == "Bearer delegated-request"
    await transport.aclose()
