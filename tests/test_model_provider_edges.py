"""Edge-case coverage for the model catalog and the native Anthropic adapter."""

import json

import httpx
import pytest

from micro_agent.models import (
    AnthropicConfig,
    AnthropicProvider,
    CatalogError,
    HttpModelCatalog,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelConfig,
)

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

_VERSION = "microagents.io/model-catalog/v1"


def _catalog(handler, **kwargs) -> HttpModelCatalog:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpModelCatalog("https://catalog.test/resolve", client=client, **kwargs)


def _model(**overrides):
    base = {"alias": "fast", "provider": "anthropic", "model_id": "claude-x"}
    return {"api_version": _VERSION, "model": {**base, **overrides}}


def test_http_catalog_resolves_and_sends_bearer_token():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_model(endpoint="https://m.test", credential_ref="K"))

    entry = _catalog(handler, token="tok").resolve("fast")
    assert entry == ModelCatalogEntry(
        alias="fast",
        provider="anthropic",
        model_id="claude-x",
        endpoint="https://m.test",
        credential_ref="K",
    )
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert json.loads(seen[0].content) == {"api_version": _VERSION, "alias": "fast"}


def test_http_catalog_unknown_alias_is_none():
    assert _catalog(lambda r: httpx.Response(404)).resolve("nope") is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(503), "HTTP 503"),
        (httpx.Response(200, content=b"not json"), "invalid JSON"),
        (httpx.Response(200, json=["x"]), "invalid response"),
        (httpx.Response(200, json={"api_version": _VERSION}), "invalid response"),
        (httpx.Response(200, json=_model(extra="x")), "invalid response"),
        (httpx.Response(200, json={**_model(), "api_version": "v0"}), "invalid response"),
        (httpx.Response(200, json={"api_version": _VERSION, "model": "x"}), "invalid response"),
        (
            httpx.Response(
                200, json={"api_version": _VERSION, "model": {"alias": "fast", "provider": "p"}}
            ),
            "invalid response",
        ),
        (httpx.Response(200, json=_model(alias="other")), "does not match"),
    ],
)
def test_http_catalog_rejects_bad_responses(response, message):
    with pytest.raises(CatalogError, match=message):
        _catalog(lambda r: response).resolve("fast")


def test_http_catalog_wraps_transport_errors_and_rejects_blank_alias():
    def boom(request):
        raise httpx.ConnectError("down")

    catalog = _catalog(boom)
    with pytest.raises(CatalogError, match="request failed"):
        catalog.resolve("fast")
    with pytest.raises(CatalogError, match="must not be empty"):
        catalog.resolve("  ")


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://catalog.test",
        "https://user:pw@catalog.test",
        "https://catalog.test/x?q=1",
        "https://catalog.test/x#frag",
        "http://catalog.example/x",
        "https://",
    ],
)
def test_http_catalog_endpoint_validation(endpoint):
    with pytest.raises(ValueError):
        HttpModelCatalog(endpoint)


def test_http_catalog_allows_loopback_http_and_validates_timeout():
    HttpModelCatalog("http://localhost:8080/resolve").close()
    with pytest.raises(ValueError, match="timeout"):
        HttpModelCatalog("https://catalog.test", timeout_seconds=0)


def test_catalog_entry_and_in_memory_validation():
    with pytest.raises(ValueError, match="alias"):
        ModelCatalogEntry(alias=" ", provider="p", model_id="m")
    with pytest.raises(ValueError, match="credential_ref"):
        ModelCatalogEntry(alias="a", provider="p", model_id="m", credential_ref=" ")
    entry = ModelCatalogEntry(alias="a", provider="p", model_id="m")
    with pytest.raises(ValueError, match="duplicate"):
        InMemoryModelCatalog([entry, entry])
    assert InMemoryModelCatalog({"a": entry}).resolve("a") is entry
    assert InMemoryModelCatalog([entry]).resolve("zzz") is None


# ---------------------------------------------------------------------------
# Anthropic adapter edges
# ---------------------------------------------------------------------------


def _anthropic(handler, **overrides) -> AnthropicProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicProvider(
        AnthropicConfig(
            endpoint="https://anthropic.test/v1",
            model_id="claude-test",
            http_client=client,
            **overrides,
        )
    )


@pytest.mark.asyncio
async def test_anthropic_merges_consecutive_roles_and_list_content():
    captured: dict[str, object] = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = _anthropic(handler)
    await provider.generate(
        ModelConfig(ref="c"),
        [
            {"role": "user", "content": "one"},
            {"role": "user", "content": [{"type": "text", "text": "two"}]},
            {"role": "assistant", "content": [{"text": "a"}, 7]},
            {"role": "assistant", "content": None},
            {"role": "mystery", "content": "folded into user"},
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "tool", "name": "echo", "content": "r2"},
        ],
    )
    await provider.aclose()

    # An endpoint already ending in /v1 is not double-prefixed.
    assert captured["url"] == "https://anthropic.test/v1/messages"
    messages = captured["payload"]["messages"]  # type: ignore[index]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == [
        {"type": "text", "text": "one"},
        {"type": "text", "text": "two"},
    ]
    # List content passes through unchanged, and the empty assistant message is
    # folded away rather than emitted as an invalid empty text block.
    assert messages[1]["content"] == [{"text": "a"}, 7]
    tool_results = [b for b in messages[2]["content"] if b.get("type") == "tool_result"]
    assert [b["tool_use_id"] for b in tool_results] == ["t1", "echo"]


@pytest.mark.asyncio
async def test_anthropic_stream_collects_text_tools_usage_and_ignores_noise():
    def handler(request):
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "ping"},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "tu1", "name": "echo"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hi"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"x":'},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": " 1}"},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": "{}"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 9},
            },
        ]
        body = "event: x\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events)
        body += "data: [DONE]\n\ndata: \n\n"
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    provider = _anthropic(handler)
    events = [e async for e in provider.stream(ModelConfig(ref="c"), [])]
    await provider.aclose()
    final = events[-1].response
    assert final is not None
    assert final.content == "hi"
    assert final.finish_reason == "tool_use"
    assert final.usage == {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}
    assert final.tool_requests == [
        {"id": "tu1", "name": "echo", "arguments": {"x": 1}},
        {"id": "tool-call", "name": "", "arguments": {}},
    ]


@pytest.mark.asyncio
async def test_anthropic_health_check_statuses():
    assert await _anthropic(lambda r: httpx.Response(200)).health_check() is True
    assert await _anthropic(lambda r: httpx.Response(503)).health_check() is False

    def boom(request):
        raise httpx.ConnectError("down")

    assert await _anthropic(boom).health_check() is False


@pytest.mark.asyncio
async def test_anthropic_response_parsing_edges():
    provider = _anthropic(
        lambda r: httpx.Response(
            200,
            json={
                "content": ["junk", {"type": "tool_use", "id": "a", "name": "n", "input": "bad"}],
            },
        )
    )
    response = await provider.generate(ModelConfig(ref="c"), [])
    assert response.tool_requests == [{"id": "a", "name": "n", "arguments": {}}]
    assert response.finish_reason == "stop"

    bad = _anthropic(lambda r: httpx.Response(200, json=["not", "an", "object"]))
    with pytest.raises(ValueError, match="JSON object"):
        await bad.generate(ModelConfig(ref="c"), [])


@pytest.mark.asyncio
async def test_anthropic_rejects_invalid_max_tokens_and_config():
    provider = _anthropic(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="max_tokens"):
        await provider.generate(ModelConfig(ref="c", generation={"max_tokens": 0}), [])
    with pytest.raises(ValueError, match="timeout"):
        AnthropicProvider(AnthropicConfig(model_id="m", timeout_seconds=0))


@pytest.mark.asyncio
async def test_anthropic_tool_call_with_text_and_bad_arguments():
    captured: dict[str, object] = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"content": []})

    provider = _anthropic(handler)
    await provider.generate(
        ModelConfig(ref="c"),
        [
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "1", "function": {"name": "a", "arguments": "{not json"}},
                    {"function": {"name": "b", "arguments": "[1]"}},
                ],
            }
        ],
    )
    await provider.aclose()
    blocks = captured["payload"]["messages"][0]["content"]  # type: ignore[index]
    assert blocks[0] == {"type": "text", "text": "calling"}
    assert blocks[1]["input"] == {"raw": "{not json"}
    assert blocks[2]["id"] == "tool-call"
    assert blocks[2]["input"] == {"value": [1]}
