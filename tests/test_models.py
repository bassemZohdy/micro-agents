"""Tests for Micro-Agent Model Support."""

import pytest

from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelResponse,
)


class TestModelConfig:
    """Test model configuration."""

    def test_basic_config(self):
        config = ModelConfig(ref="test-model")
        assert config.ref == "test-model"
        assert config.provider is None
        assert config.endpoint is None

    def test_full_config(self):
        config = ModelConfig(
            ref="gpt-4",
            provider="openai",
            model_id="gpt-4-turbo",
            endpoint="https://api.openai.com",
            generation={"temperature": 0.2},
            timeout_seconds=30,
        )
        assert config.provider == "openai"
        assert config.generation["temperature"] == 0.2


class TestModelResponse:
    """Test model response."""

    def test_default_response(self):
        resp = ModelResponse()
        assert resp.content == ""
        assert resp.finish_reason == "stop"
        assert resp.tool_requests == []

    def test_response_with_content(self):
        resp = ModelResponse(content="hello", usage={"total": 100})
        assert resp.content == "hello"
        assert resp.usage["total"] == 100


class TestFakeModelProvider:
    """Test deterministic fake model."""

    @pytest.mark.asyncio
    async def test_basic_response(self):
        provider = FakeModelProvider()
        config = ModelConfig(ref="fake")
        response = await provider.generate(config, [{"role": "user", "content": "hi"}])
        assert response.content == "fake response"
        assert response.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_custom_response(self):
        provider = FakeModelProvider(FakeModelConfig(response="custom answer"))
        config = ModelConfig(ref="fake")
        response = await provider.generate(config, [{"role": "user", "content": "hi"}])
        assert response.content == "custom answer"

    @pytest.mark.asyncio
    async def test_tool_requests(self):
        tool_req = {"name": "check_eligibility", "arguments": {"user_id": "123"}}
        provider = FakeModelProvider(FakeModelConfig(tool_requests=[tool_req]))
        config = ModelConfig(ref="fake")
        response = await provider.generate(config, [{"role": "user", "content": "check"}])
        assert len(response.tool_requests) == 1
        assert response.tool_requests[0]["name"] == "check_eligibility"

    @pytest.mark.asyncio
    async def test_error_mode(self):
        provider = FakeModelProvider(FakeModelConfig(should_error=True, error_message="boom"))
        config = ModelConfig(ref="fake")
        with pytest.raises(RuntimeError, match="boom"):
            await provider.generate(config, [{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_invocation_recording(self):
        provider = FakeModelProvider()
        config = ModelConfig(ref="fake")
        messages = [{"role": "user", "content": "test"}]
        await provider.generate(config, messages)
        assert len(provider.invocations) == 1
        assert provider.invocations[0]["messages"] == messages

    @pytest.mark.asyncio
    async def test_health_check(self):
        provider = FakeModelProvider()
        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_usage_tracking(self):
        provider = FakeModelProvider(
            FakeModelConfig(usage={"prompt_tokens": 100, "completion_tokens": 50})
        )
        config = ModelConfig(ref="fake")
        response = await provider.generate(config, [{"role": "user", "content": "hi"}])
        assert response.usage["prompt_tokens"] == 100
        assert response.usage["completion_tokens"] == 50


class TestOpenAICompatProvider:
    """Wire-contract behavior of the OpenAI-compatible provider."""

    def _provider(self, client) -> object:
        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider

        return OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.test/v1",
                model_id="test-model",
                http_client=client,
            )
        )

    @pytest.mark.asyncio
    async def test_tool_call_ids_are_preserved_from_the_wire(self):
        import json as jsonlib

        import httpx

        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = jsonlib.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_wire_1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo",
                                            "arguments": '{"message": "hi"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"total_tokens": 9},
                },
            )

        client = httpx.AsyncClient(
            base_url="https://llm.example.test/v1", transport=httpx.MockTransport(handler)
        )
        provider = OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.test/v1",
                model_id="test-model",
                http_client=client,
            )
        )
        response = await provider.generate(
            _model_config(),
            messages=[],
            tools=[
                {
                    "name": "echo",
                    "description": "Echo",
                    "input_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                }
            ],
        )
        await provider.aclose()
        assert response.tool_requests[0]["id"] == "call_wire_1"
        assert response.tool_requests[0]["name"] == "echo"
        assert response.tool_requests[0]["arguments"] == {"message": "hi"}
        # The provider sends the standard function-tool shape.
        assert captured["payload"]["tools"][0]["type"] == "function"

    @pytest.mark.asyncio
    async def test_injected_client_is_used_and_not_closed(self):
        import httpx

        client = httpx.AsyncClient(
            base_url="https://llm.example.test/v1",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"choices": []})
            ),
        )
        provider = self._provider(client)
        await provider.generate(_model_config(), messages=[])
        await provider.aclose()
        # The injected client stays usable — the provider does not own it.
        assert not client.is_closed
        await client.aclose()

    @pytest.mark.otel
    @pytest.mark.asyncio
    async def test_injects_w3c_context_on_chat_completion(self):
        pytest.importorskip("opentelemetry.sdk")
        import httpx
        from opentelemetry.sdk.trace import TracerProvider

        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider
        from micro_agent.observability import Telemetry

        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["traceparent"] = request.headers.get("traceparent")
            return httpx.Response(200, json={"choices": []})

        client = httpx.AsyncClient(
            base_url="https://llm.example.test/v1", transport=httpx.MockTransport(handler)
        )
        tracer_provider = TracerProvider()
        telemetry = Telemetry(otel_tracer=tracer_provider.get_tracer("model-test"))
        provider = OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.test/v1",
                model_id="test-model",
                http_client=client,
                telemetry=telemetry,
            )
        )
        span = telemetry.start_span("model.generate")
        try:
            await provider.generate(_model_config(), messages=[])
        finally:
            telemetry.finish_span(span)
            await provider.aclose()

        assert captured["traceparent"] is not None
        assert captured["traceparent"].startswith("00-")

    def test_capabilities_report_tool_use(self):
        import httpx

        provider = self._provider(
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
            )
        )
        assert provider.capabilities().tool_use is True
        assert provider.capabilities().streaming is False

    def test_tls_and_proxy_options_reach_the_built_client(self):
        import ssl

        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider

        provider = OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.test/v1",
                model_id="test-model",
                verify_tls=False,
                proxy="http://proxy.example.test:3128",
            )
        )
        try:
            assert provider._client._transport._pool._ssl_context.verify_mode == ssl.CERT_NONE
        finally:
            import asyncio

            asyncio.run(provider.aclose())


def _model_config():
    from micro_agent.models import ModelConfig

    return ModelConfig(ref="test-model", model_id="test-model")
