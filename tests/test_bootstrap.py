"""Executable bootstrap tests for definition and environment model selection."""

import pytest

from micro_agent.config import BootstrapError, build_runtime
from micro_agent.definition import load_definition_from_dict
from micro_agent.models import FakeModelProvider, OpenAICompatProvider


def _definition(**model: object):
    model_data = {"ref": "fake-model", **model}
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {"model": model_data},
            },
        }
    )


def test_explicit_fake_provider_uses_fake_provider():
    bootstrap = build_runtime(_definition(provider="fake"))
    try:
        assert isinstance(bootstrap.runtime._model_provider, FakeModelProvider)
        assert bootstrap.resolved.model_ref == "fake-model"
    finally:
        # FakeModelProvider has no close hook, but close keeps this test aligned
        # with the lifecycle used by the executable process.
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_bare_string_model_reference_requires_explicit_provider():
    with pytest.raises(BootstrapError, match="provider is not configured"):
        build_runtime(_definition())


@pytest.mark.asyncio
async def test_definition_selects_openai_compatible_provider():
    bootstrap = build_runtime(
        _definition(
            ref="reasoning-model",
            provider="openai-compatible",
            endpoint="https://llm.example.test/v1",
            timeout_seconds=12,
        )
    )
    try:
        provider = bootstrap.runtime._model_provider
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._config.endpoint == "https://llm.example.test/v1"
        assert provider._config.model_id == "reasoning-model"
        assert provider._config.timeout_seconds == 12.0
    finally:
        await bootstrap.runtime.close()


@pytest.mark.asyncio
async def test_environment_overrides_select_live_provider(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MICRO_AGENT_MODEL_ENDPOINT", "https://env.example.test/v1")
    monkeypatch.setenv("MICRO_AGENT_MODEL_ID", "env-model")
    monkeypatch.setenv("MODEL_TOKEN", "secret-token")
    bootstrap = build_runtime(_definition(ref="definition-model", credential_ref="MODEL_TOKEN"))
    try:
        provider = bootstrap.runtime._model_provider
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._config.endpoint == "https://env.example.test/v1"
        assert provider._config.model_id == "env-model"
        assert provider._config.api_key == "secret-token"
        assert bootstrap.resolved.model_api_key == "secret-token"
    finally:
        await bootstrap.runtime.close()


def test_live_provider_requires_endpoint():
    with pytest.raises(BootstrapError, match="requires model_endpoint"):
        build_runtime(_definition(ref="reasoning-model", provider="openai"))


def test_unknown_provider_fails_before_startup():
    with pytest.raises(BootstrapError, match="Unsupported model provider"):
        build_runtime(_definition(ref="reasoning-model", provider="unknown"))


def test_missing_definition_credential_fails_without_secret_leak(monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    with pytest.raises(BootstrapError) as exc_info:
        build_runtime(
            _definition(
                ref="reasoning-model",
                provider="openai",
                endpoint="https://llm.example.test/v1",
                credential_ref="MISSING_TOKEN",
            )
        )
    message = str(exc_info.value)
    assert "MISSING_TOKEN" in message
    assert "secret" not in message.lower()
