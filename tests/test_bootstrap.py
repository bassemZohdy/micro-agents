"""Executable bootstrap tests for definition and environment model selection."""

import pytest

from micro_agent.config import BootstrapError, build_runtime
from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.memory import InMemoryMemoryProvider
from micro_agent.models import FakeModelProvider, OpenAICompatProvider
from micro_agent.session import InMemorySessionProvider, SqliteSessionProvider


def _definition(
    *,
    session: dict[str, object] | None = None,
    memory: dict[str, object] | None = None,
    **model: object,
):
    model_data = {"ref": "fake-model", **model}
    dependencies: dict[str, object] = {"model": model_data}
    if session is not None:
        dependencies["session"] = session
    if memory is not None:
        dependencies["memory"] = memory
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": dependencies,
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
            model_id="gpt-4o-mini",
            provider="openai-compatible",
            endpoint="https://llm.example.test/v1",
            timeout_seconds=12,
        )
    )
    try:
        provider = bootstrap.runtime._model_provider
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._config.endpoint == "https://llm.example.test/v1"
        assert provider._config.model_id == "gpt-4o-mini"
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


def test_live_provider_requires_provider_model_id():
    with pytest.raises(BootstrapError, match="model_id"):
        build_runtime(
            _definition(
                ref="logical-alias",
                provider="openai",
                endpoint="https://llm.example.test/v1",
            )
        )


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


@pytest.mark.asyncio
async def test_memory_session_persistence_is_constructed_from_definition():
    bootstrap = build_runtime(
        _definition(provider="fake", session={"persistence": "memory", "ttl_seconds": 60})
    )
    try:
        assert isinstance(bootstrap.runtime._config.session_provider, InMemorySessionProvider)
        assert bootstrap.runtime._config.session_provider._ttl_seconds == 60
    finally:
        await bootstrap.runtime.close()


@pytest.mark.asyncio
async def test_sqlite_session_endpoint_is_constructed_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setenv("MICRO_AGENT_SESSION_ENDPOINT", f"sqlite:///{path}")
    bootstrap = build_runtime(
        _definition(provider="fake", session={"persistence": "sqlite", "ttl_seconds": 30})
    )
    try:
        provider = bootstrap.runtime._config.session_provider
        assert isinstance(provider, SqliteSessionProvider)
        await provider.create("bootstrap-session")
        assert path.exists()
    finally:
        await bootstrap.runtime.close()


def test_external_session_persistence_fails_before_runtime_creation(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_SESSION_ENDPOINT", "https://sessions.example.test")
    with pytest.raises(BootstrapError, match="external session provider"):
        build_runtime(_definition(provider="fake", session={"persistence": "external"}))


@pytest.mark.asyncio
async def test_declared_memory_dependency_constructs_builtin_provider():
    bootstrap = build_runtime(
        _definition(provider="fake", memory={"ref": "agent-memory", "scope": "agent"})
    )
    try:
        assert isinstance(bootstrap.runtime._config.memory_provider, InMemoryMemoryProvider)
        assert bootstrap.runtime.capabilities().memory is True
    finally:
        await bootstrap.runtime.close()


@pytest.mark.asyncio
async def test_bootstrapped_session_provider_is_used_by_invocation():
    definition = _definition(provider="fake", session={"persistence": "memory"})
    bootstrap = build_runtime(definition)
    agent = DefaultMicroAgent(definition, bootstrap.runtime)
    try:
        await agent.initialize()
        await agent.start()
        await agent.invoke(AgentRequest(input={"message": "hello"}, session_id="session-1"))
        provider = bootstrap.runtime._config.session_provider
        assert isinstance(provider, InMemorySessionProvider)
        session = await provider.get("session-1")
        assert session is not None
        assert len(session.messages) == 2
    finally:
        await agent.stop()
        await agent.shutdown()
        await bootstrap.runtime.close()


def test_external_memory_endpoint_fails_before_runtime_creation(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_MEMORY_ENDPOINT", "https://memory.example.test")
    with pytest.raises(BootstrapError, match="external memory"):
        build_runtime(_definition(provider="fake", memory={"ref": "agent-memory"}))


def test_state_endpoint_without_definition_is_not_silently_ignored(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_SESSION_ENDPOINT", "sqlite:///:memory:")
    with pytest.raises(BootstrapError, match="persistence is 'none'"):
        build_runtime(_definition(provider="fake"))
