"""Wire-contract tests for the Azure OpenAI provider."""

import json

import httpx
import pytest

from micro_agent.models import (
    DEFAULT_AZURE_API_VERSION,
    AzureOpenAIConfig,
    AzureOpenAIProvider,
    ModelConfig,
)


def _provider(handler, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AzureOpenAIProvider(
        AzureOpenAIConfig(
            endpoint="https://res.openai.azure.test/",
            model_id="gpt-deploy",
            api_key="azure-key",
            http_client=client,
            **overrides,
        )
    )


@pytest.mark.asyncio
async def test_generate_uses_deployment_url_and_api_version():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "hi",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "echo", "arguments": '{"x": 1}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    provider = _provider(handler)
    response = await provider.generate(
        ModelConfig(ref="azure"),
        [{"role": "user", "content": "hello"}],
        tools=[{"name": "echo", "description": "Echo", "input_schema": {"type": "object"}}],
    )
    await provider.aclose()

    request = captured["request"]
    assert request.url.path == "/openai/deployments/gpt-deploy/chat/completions"
    assert request.url.params["api-version"] == DEFAULT_AZURE_API_VERSION
    payload = json.loads(request.content)
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["tools"][0]["function"]["name"] == "echo"
    assert response.content == "hi"
    assert response.tool_requests == [{"id": "c1", "name": "echo", "arguments": {"x": 1}}]
    assert response.usage["total_tokens"] == 5


@pytest.mark.asyncio
async def test_model_config_deployment_overrides_default_and_is_url_quoted():
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler, api_version="2025-01-01")
    await provider.generate(ModelConfig(ref="m", model_id="a/b c"), [])
    await provider.aclose()

    request = captured["request"]
    assert request.url.raw_path.startswith(b"/openai/deployments/a%2Fb%20c/chat/completions")
    assert request.url.params["api-version"] == "2025-01-01"


@pytest.mark.asyncio
async def test_api_key_header_not_bearer_when_provider_builds_client():
    provider = AzureOpenAIProvider(
        AzureOpenAIConfig(
            endpoint="https://res.openai.azure.test",
            model_id="dep",
            api_key="azure-key",
            default_headers={"x-custom": "1"},
        )
    )
    headers = provider._client.headers
    assert headers["api-key"] == "azure-key"
    assert "authorization" not in headers
    assert headers["x-custom"] == "1"
    await provider.aclose()


@pytest.mark.asyncio
async def test_stream_and_health_use_azure_urls():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        body = (
            'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    provider = _provider(handler)
    events = [e async for e in provider.stream(ModelConfig(ref="m"), [])]
    assert await provider.health_check() is True
    await provider.aclose()

    assert [e.delta for e in events if e.delta] == ["he", "llo"]
    assert events[-1].response is not None
    assert events[-1].response.content == "hello"
    assert seen[0].url.path == "/openai/deployments/gpt-deploy/chat/completions"
    assert seen[1].url.path == "/openai/models"
    assert seen[1].url.params["api-version"] == DEFAULT_AZURE_API_VERSION


def test_capabilities_match_chat_completions_surface():
    provider = _provider(lambda request: httpx.Response(200))
    caps = provider.capabilities()
    assert (caps.tool_use, caps.streaming, caps.structured_output) == (True, True, True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"endpoint": "", "model_id": "d"},
        {"endpoint": "https://x", "model_id": ""},
        {"endpoint": "https://x", "model_id": "default"},
        {"endpoint": "https://x", "model_id": "d", "api_version": ""},
        {"endpoint": "https://x", "model_id": "d", "timeout_seconds": 0},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        AzureOpenAIProvider(AzureOpenAIConfig(**kwargs))
