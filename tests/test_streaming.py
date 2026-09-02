"""Streaming capability and transport acceptance tests."""

import json

import httpx
import pytest

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.models import (
    FakeModelConfig,
    ModelConfig,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


@pytest.mark.asyncio
async def test_openai_compat_stream_parses_sse_deltas_and_final_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        body = "\n\n".join(
            [
                'data: {"choices":[{"delta":{"content":"Hel"},"finish_reason":null}]}',
                (
                    'data: {"choices":[{"delta":{"content":"lo"},'
                    '"finish_reason":"stop"}],"usage":{"completion_tokens":2}}'
                ),
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatProvider(
        OpenAICompatConfig(endpoint="https://model.example/v1", http_client=client)
    )
    events = [
        event
        async for event in provider.stream(
            ModelConfig(ref="model", model_id="test"),
            [{"role": "user", "content": "hi"}],
        )
    ]
    assert [event.delta for event in events if event.delta] == ["Hel", "lo"]
    final = events[-1].response
    assert final is not None
    assert final.content == "Hello"
    assert final.finish_reason == "stop"
    assert final.usage == {"completion_tokens": 2}
    await client.aclose()


@pytest.mark.asyncio
async def test_http_invoke_streams_sse_when_runtime_truthfully_supports_it() -> None:
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "stream-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Stream the answer."},
                "dependencies": {"model": {"ref": "fake-model"}},
            },
        }
    )
    runtime = AdkRuntime(
        AdkRuntimeConfig(
            fake_model_config=FakeModelConfig(
                response="hello",
                stream_chunks=["hel", "lo"],
            )
        )
    )
    agent = DefaultMicroAgent(definition, runtime)
    await agent.initialize()
    await agent.start()
    assert agent.runtime_capabilities.streaming is True

    app = create_app(agent)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream(
            "POST",
            "/v1/invoke",
            headers={"Accept": "text/event-stream"},
            json={"input": {"q": "hi"}, "request_id": "stream-1"},
        ) as response,
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = await response.aread()

    text = body.decode()
    assert 'event: delta\ndata: {"delta":"hel"}' in text
    assert 'event: delta\ndata: {"delta":"lo"}' in text
    assert "event: final" in text
    assert '"request_id":"stream-1"' in text
    assert '"content":"hello"' in text

    await agent.stop()
    await agent.shutdown()
    await runtime.close()
