"""Strict-contract edges for downstream token delegation."""

from __future__ import annotations

import httpx
import pytest

from micro_agent.security import (
    DelegatedToken,
    HttpTokenExchangeProvider,
    TokenExchangeError,
)


def _provider(handler, **kwargs) -> HttpTokenExchangeProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTokenExchangeProvider("https://issuer.example.test/token", client=client, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"access_token": ""},
        {"access_token": "   "},
        {"access_token": 5},
        {"access_token": "t", "token_type": "MAC"},
        {"access_token": "t", "token_type": 1},
        {"access_token": "t", "expires_in": 0},
        {"access_token": "t", "expires_in": True},
        {"access_token": "t", "expires_in": "60"},
    ],
)
def test_delegated_token_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        DelegatedToken(**kwargs)


def test_delegated_token_accepts_case_insensitive_bearer():
    token = DelegatedToken("abc", token_type="bearer", expires_in=30)
    assert (token.access_token, token.expires_in) == ("abc", 30)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://issuer.example.test/token",
        "https://",
        "https://user:pw@issuer.example.test/token",
        "https://issuer.example.test/token?x=1",
        "https://issuer.example.test/token#frag",
        "http://issuer.example.test/token",
    ],
)
def test_endpoint_must_be_a_safe_https_url(endpoint):
    with pytest.raises(ValueError):
        HttpTokenExchangeProvider(endpoint)


def test_loopback_http_is_allowed_and_timeout_is_validated():
    HttpTokenExchangeProvider("http://127.0.0.1:9000/token")
    with pytest.raises(ValueError, match="timeout_seconds"):
        HttpTokenExchangeProvider("https://issuer.example.test/token", timeout_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["", "   "])
async def test_blank_audience_is_rejected_before_any_request(audience):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    provider = _provider(handler)
    with pytest.raises(TokenExchangeError, match="audience"):
        await provider.exchange(audience=audience)
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500), "request failed"),
        (httpx.Response(200, content=b"not json"), "request failed"),
        (httpx.Response(200, json=["x"]), "must be an object"),
        (httpx.Response(200, json={"token_type": "Bearer"}), "invalid contract"),
        (httpx.Response(200, json={"access_token": "t", "refresh_token": "r"}), "invalid contract"),
        (httpx.Response(200, json={"access_token": ""}), "invalid token"),
        (httpx.Response(200, json={"access_token": "t", "token_type": "MAC"}), "invalid token"),
        (httpx.Response(200, json={"access_token": "t", "expires_in": 0}), "invalid token"),
    ],
)
async def test_exchange_rejects_non_conforming_responses(response, message):
    provider = _provider(lambda request: response)
    with pytest.raises(TokenExchangeError, match=message):
        await provider.exchange(audience="https://mcp.example.test")
    await provider.aclose()


@pytest.mark.asyncio
async def test_transport_errors_are_wrapped_without_leaking_the_actor_token():
    def boom(request):
        raise httpx.ConnectError("connection to issuer failed secret-actor")

    provider = _provider(boom)
    with pytest.raises(TokenExchangeError) as info:
        await provider.exchange(audience="aud", actor_token="secret-actor")
    assert "secret-actor" not in str(info.value)
    await provider.aclose()


@pytest.mark.asyncio
async def test_successful_exchange_returns_a_typed_token_without_identity():
    provider = _provider(
        lambda request: httpx.Response(
            200, json={"access_token": "delegated", "token_type": "bearer", "expires_in": 60}
        )
    )
    token = await provider.exchange(audience="aud")
    assert (token.access_token, token.expires_in) == ("delegated", 60)
    await provider.aclose()


@pytest.mark.asyncio
async def test_owned_client_is_closed_and_injected_client_is_not():
    owned = HttpTokenExchangeProvider("https://issuer.example.test/token")
    await owned.aclose()
    assert owned._client.is_closed

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    injected = HttpTokenExchangeProvider("https://issuer.example.test/token", client=client)
    await injected.aclose()
    assert not client.is_closed
    await client.aclose()
