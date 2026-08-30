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
