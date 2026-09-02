"""Tests for model provider and configuration contracts."""

import pytest

from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelConfig,
    ModelResponse,
    ProviderCapabilities,
)


def _model_config(**overrides):
    values = {"ref": "test-model", "model_id": "test-model"}
    values.update(overrides)
    return ModelConfig(**values)


class TestModelConfig:
    def test_minimal(self):
        config = ModelConfig(ref="model")
        assert config.ref == "model"
        assert config.generation == {}

    def test_full(self):
        config = ModelConfig(
            ref="primary",
            provider="openai-compatible",
            model_id="gpt-test",
            endpoint="https://example.test/v1",
            credential_ref="secret",
            generation={"temperature": 0.1},
            timeout_seconds=15,
            capabilities=["tool_use"],
        )
        assert config.model_id == "gpt-test"
        assert config.capabilities == ["tool_use"]


class TestProviderCapabilities:
    def test_conservative_defaults(self):
        capabilities = ProviderCapabilities()
        assert capabilities.tool_use is False
        assert capabilities.streaming is False
        assert capabilities.structured_output is False


class TestFakeModelProvider:
    @pytest.mark.asyncio
    async def test_returns_configured_response(self):
        provider = FakeModelProvider(FakeModelConfig(response="hello"))
        result = await provider.generate(_model_config(), [{"role": "user", "content": "hi"}])
        assert isinstance(result, ModelResponse)
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_returns_tool_requests(self):
        provider = FakeModelProvider(
            FakeModelConfig(tool_requests=[{"name": "echo", "arguments": {"value": "hi"}}])
        )
        result = await provider.generate(_model_config(), [])
        assert result.tool_requests[0]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_can_fail(self):
        provider = FakeModelProvider(FakeModelConfig(should_error=True, error_message="boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await provider.generate(_model_config(), [])

    @pytest.mark.asyncio
    async def test_records_invocations(self):
        provider = FakeModelProvider()
        await provider.generate(_model_config(), [{"role": "user", "content": "hi"}])
        assert len(provider.invocations) == 1
        assert provider.invocations[0]["messages"][0]["content"] == "hi"

    def test_capabilities_report_tool_use_only_by_default(self):
        provider = FakeModelProvider()
        assert provider.capabilities().tool_use is True
        assert provider.capabilities().streaming is False

    @pytest.mark.asyncio
    async def test_health_check(self):
        provider = FakeModelProvider()
        assert await provider.health_check() is True


class TestOpenAICompatProvider:
    def _provider(self, client, *, telemetry=None):
        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider

        return OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint="https://llm.example.test/v1",
                model_id="test-model",
                http_client=client,
                telemetry=telemetry,
            )
        )

    @pytest.mark.asyncio
    async def test_generate_maps_chat_completion(self):
        import httpx

        async def handler(request):
            payload = __import__("json").loads(request.content)
            assert payload["model"] == "test-model"
            assert payload["messages"][0]["content"] == "hi"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "hello"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = self._provider(client)
        try:
            result = await provider.generate(
                _model_config(), [{"role": "user", "content": "hi"}]
            )
        finally:
            await client.aclose()
        assert result.content == "hello"
        assert result.usage == {"prompt_tokens": 3, "completion_tokens": 1}

    @pytest.mark.asyncio
    async def test_generate_maps_tool_calls(self):
        import httpx

        async def handler(request):
            payload = __import__("json").loads(request.content)
            assert payload["tools"][0]["function"]["name"] == "echo"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "echo",
                                            "arguments": '{"value":"hi"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = self._provider(client)
        try:
            result = await provider.generate(
                _model_config(),
                [{"role": "user", "content": "hi"}],
                tools=[
                    {
                        "name": "echo",
                        "description": "Echo",
                        "input_schema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                        },
                    }
                ],
            )
        finally:
            await client.aclose()
        assert result.tool_requests == [
            {"id": "call-1", "name": "echo", "arguments": {"value": "hi"}}
        ]

    @pytest.mark.asyncio
    async def test_invalid_tool_arguments_are_preserved_as_raw(self):
        import httpx

        async def handler(_request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {"name": "echo", "arguments": "not-json"},
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = self._provider(client)
        try:
            result = await provider.generate(_model_config(), [])
        finally:
            await client.aclose()
        assert result.tool_requests[0]["arguments"] == {"raw": "not-json"}

    @pytest.mark.asyncio
    async def test_health_check(self):
        import httpx

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        )
        provider = self._provider(client)
        try:
            assert await provider.health_check() is True
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_health_check_handles_network_error(self):
        import httpx

        def handler(request):
            raise httpx.ConnectError("offline", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = self._provider(client)
        try:
            assert await provider.health_check() is False
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_trace_context_is_injected_into_provider_request(self):
        import httpx

        from micro_agent.observability import Telemetry

        captured = {}

        def handler(request):
            captured["traceparent"] = request.headers.get("traceparent")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        telemetry = Telemetry()
        provider = self._provider(client, telemetry=telemetry)
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
        assert provider.capabilities().streaming is True

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
        transport = provider._client._transport
        assert transport._pool._proxy is not None
        ssl_context = transport._pool._ssl_context
        assert isinstance(ssl_context, ssl.SSLContext)
        assert ssl_context.verify_mode == ssl.CERT_NONE

    @pytest.mark.asyncio
    async def test_aclose_closes_owned_client(self):
        from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider

        provider = OpenAICompatProvider(
            OpenAICompatConfig(endpoint="https://llm.example.test/v1")
        )
        assert provider._client.is_closed is False
        await provider.aclose()
        assert provider._client.is_closed is True
