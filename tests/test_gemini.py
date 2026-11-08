"""Wire-contract tests for the native Gemini provider."""

import json

import httpx
import pytest

from micro_agent.models import GeminiConfig, GeminiProvider, ModelConfig


def _provider(handler, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiProvider(
        GeminiConfig(
            endpoint="https://gemini.test",
            model_id="gemini-test",
            api_key="gem-key",
            http_client=client,
            **overrides,
        )
    )


def _ok(parts, **extra):
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 3},
            **extra,
        },
    )


@pytest.mark.asyncio
async def test_translates_system_tools_and_tool_results():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return _ok(
            [
                {"text": "thinking...", "thought": True},
                {"text": "done"},
                {"functionCall": {"name": "echo", "args": {"x": 2}}},
            ]
        )

    provider = _provider(handler)
    response = await provider.generate(
        ModelConfig(ref="g", generation={"max_tokens": 42, "top_p": 0.5, "temperature": 0.1}),
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "call echo"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "t1", "function": {"name": "echo", "arguments": '{"x": 1}'}},
                    {"id": "t2", "function": {"name": "echo", "arguments": {"x": 9}}},
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": '{"x": 1}'},
            {"role": "tool", "tool_call_id": "t2", "content": "plain text"},
        ],
        tools=[
            {
                "name": "echo",
                "description": "Echo",
                "input_schema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"x": {"type": ["integer", "null"], "default": 1}},
                    "required": ["x"],
                },
            },
            {"name": "noop", "description": "No args", "input_schema": {"type": "object"}},
        ],
    )
    await provider.aclose()

    request = captured["request"]
    assert request.url.path == "/v1beta/models/gemini-test:generateContent"
    assert request.headers["x-goog-api-key"] == "gem-key"
    assert "gem-key" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert payload["generationConfig"] == {
        "maxOutputTokens": 42,
        "topP": 0.5,
        "temperature": 0.1,
    }
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]
    assert payload["contents"][1]["parts"] == [
        {"functionCall": {"name": "echo", "args": {"x": 1}}},
        {"functionCall": {"name": "echo", "args": {"x": 9}}},
    ]
    # Both tool results fold into one user turn and resolve the function name.
    assert payload["contents"][2]["parts"] == [
        {"functionResponse": {"name": "echo", "response": {"x": 1}}},
        {"functionResponse": {"name": "echo", "response": {"result": "plain text"}}},
    ]
    declarations = payload["tools"][0]["functionDeclarations"]
    assert declarations[0]["parameters"] == {
        "type": "object",
        "properties": {"x": {"type": "integer", "nullable": True}},
        "required": ["x"],
    }
    assert "parameters" not in declarations[1]

    assert response.content == "done"
    assert response.finish_reason == "stop"
    assert response.tool_requests == [
        {"id": "gemini-call-0", "name": "echo", "arguments": {"x": 2}}
    ]
    assert response.usage == {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}


@pytest.mark.asyncio
async def test_model_path_accepts_models_prefix_and_per_call_override():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _ok([{"text": "x"}])

    provider = _provider(handler)
    await provider.generate(ModelConfig(ref="g", model_id="models/gemini-other"), [])
    await provider.aclose()
    assert seen == ["/v1beta/models/gemini-other:generateContent"]


@pytest.mark.asyncio
async def test_finish_reason_mapping_and_blocked_prompt():
    def length(request):
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "a"}]}, "finishReason": "MAX_TOKENS"}
                ]
            },
        )

    def blocked(request):
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    provider = _provider(length)
    assert (await provider.generate(ModelConfig(ref="g"), [])).finish_reason == "length"
    await provider.aclose()

    provider = _provider(blocked)
    result = await provider.generate(ModelConfig(ref="g"), [])
    await provider.aclose()
    assert result.finish_reason == "content_filter"
    assert result.content == ""
    assert result.metadata["block_reason"] == "SAFETY"


@pytest.mark.asyncio
async def test_stream_accumulates_text_tool_calls_and_usage():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        chunks = [
            {"candidates": [{"content": {"parts": [{"text": "he"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "llo"}]}}]},
            {
                "candidates": [
                    {
                        "content": {"parts": [{"functionCall": {"name": "echo", "args": {}}}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
            },
        ]
        body = "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks)
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    provider = _provider(handler)
    events = [e async for e in provider.stream(ModelConfig(ref="g"), [])]
    await provider.aclose()

    assert seen[0].url.path == "/v1beta/models/gemini-test:streamGenerateContent"
    assert seen[0].url.params["alt"] == "sse"
    assert [e.delta for e in events if e.delta] == ["he", "llo"]
    final = events[-1].response
    assert final is not None
    assert final.content == "hello"
    assert final.tool_requests == [{"id": "gemini-call-0", "name": "echo", "arguments": {}}]
    assert final.usage["total_tokens"] == 3
    assert final.finish_reason == "stop"


@pytest.mark.asyncio
async def test_health_check_and_http_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={})
        return httpx.Response(429, json={"error": "quota"})

    provider = _provider(handler)
    assert await provider.health_check() is True
    with pytest.raises(httpx.HTTPStatusError):
        await provider.generate(ModelConfig(ref="g"), [])
    await provider.aclose()

    def down(request):
        raise httpx.ConnectError("down")

    provider = _provider(down)
    assert await provider.health_check() is False
    await provider.aclose()


def test_capabilities_and_endpoint_normalisation():
    provider = GeminiProvider(
        GeminiConfig(
            endpoint="https://gemini.test/v1beta/",
            model_id="m",
            http_client=httpx.AsyncClient(),
        )
    )
    assert provider._base == "https://gemini.test/v1beta"
    caps = provider.capabilities()
    assert (caps.tool_use, caps.streaming, caps.structured_output) == (True, True, False)


@pytest.mark.parametrize("kwargs", [{"model_id": ""}, {"model_id": "m", "timeout_seconds": 0}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        GeminiProvider(GeminiConfig(**kwargs))
