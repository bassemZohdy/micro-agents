"""Executable bootstrap tests for definition and environment model selection."""

import pytest

from micro_agent.config import BootstrapError, EnvironmentOverlay, build_runtime
from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.memory import InMemoryMemoryProvider
from micro_agent.models import FakeModelProvider, OpenAICompatProvider
from micro_agent.security import AgentPolicy
from micro_agent.session import InMemorySessionProvider, RedisSessionProvider, SqliteSessionProvider
from runtimes.adk import AdkRuntime
from runtimes.google_adk import GoogleAdkRuntime


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


def test_default_runtime_is_custom_reference_loop():
    bootstrap = build_runtime(_definition(provider="fake"))
    try:
        assert isinstance(bootstrap.runtime, AdkRuntime)
        assert bootstrap.resolved.runtime is None
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_google_adk_runtime_is_selected_from_deployment_environment(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    bootstrap = build_runtime(_definition(provider="fake"))
    try:
        assert isinstance(bootstrap.runtime, GoogleAdkRuntime)
        assert bootstrap.resolved.runtime == "google-adk"
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_google_adk_runtime_accepts_native_google_model_selection(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google_adk")
    bootstrap = build_runtime(_definition(provider="google", model_id="gemini-2.5-flash"))
    try:
        assert isinstance(bootstrap.runtime, GoogleAdkRuntime)
        assert bootstrap.runtime._model_provider is None
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_unsupported_runtime_fails_before_runtime_creation(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "unknown")
    with pytest.raises(BootstrapError, match="Unsupported runtime"):
        build_runtime(_definition(provider="fake"))


def test_google_adk_runtime_accepts_in_memory_session_persistence(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    bootstrap = build_runtime(
        _definition(provider="fake", session={"persistence": "memory", "ttl_seconds": 60})
    )
    try:
        assert isinstance(bootstrap.runtime, GoogleAdkRuntime)
        assert bootstrap.runtime.capabilities().memory is False
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_google_adk_runtime_rejects_sqlite_session_persistence(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    monkeypatch.setenv("MICRO_AGENT_SESSION_ENDPOINT", "sqlite:///:memory:")
    with pytest.raises(BootstrapError, match="only in-memory sessions"):
        build_runtime(_definition(provider="fake"))


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
async def test_environment_overlay_binds_model_endpoint_without_mutating_definition():
    definition = _definition(
        ref="logical-model",
        model_id="staging-model",
        provider="openai-compatible",
        endpoint="https://logical-model.example.test/v1",
    )
    bootstrap = build_runtime(
        definition,
        environment=EnvironmentOverlay(model_endpoint="https://staging-model.example.test/v1"),
    )
    try:
        provider = bootstrap.runtime._model_provider
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._config.endpoint == "https://staging-model.example.test/v1"
        assert definition.spec.dependencies.model is not None
        assert (
            definition.spec.dependencies.model.endpoint == "https://logical-model.example.test/v1"
        )
    finally:
        await bootstrap.runtime.close()


def test_mcp_overlay_binding_must_reference_declared_server():
    with pytest.raises(BootstrapError, match="undeclared server"):
        build_runtime(
            _definition(provider="fake"),
            environment=EnvironmentOverlay(
                mcp_endpoints={"missing-server": "https://mcp.example.test"}
            ),
        )


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
async def test_external_redis_session_persistence_is_constructed(monkeypatch):
    import types

    class FakeRedisClient:
        def pipeline(self, *, transaction: bool = False):
            del transaction
            raise AssertionError("bootstrap test should not issue Redis commands")

    fake_client = FakeRedisClient()
    monkeypatch.setattr(
        "micro_agent.session.redis._import_redis",
        lambda: types.SimpleNamespace(from_url=lambda *_args, **_kwargs: fake_client),
    )
    monkeypatch.setenv("MICRO_AGENT_SESSION_ENDPOINT", "rediss://sessions.example.test/0")
    bootstrap = build_runtime(
        _definition(provider="fake", session={"persistence": "external", "ttl_seconds": 60})
    )
    try:
        provider = bootstrap.runtime._config.session_provider
        assert isinstance(provider, RedisSessionProvider)
        assert provider._endpoint == "rediss://sessions.example.test/0"
    finally:
        await bootstrap.runtime.close()


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


def test_google_adk_runtime_maps_declared_memory_dependency(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    bootstrap = build_runtime(
        _definition(provider="fake", memory={"ref": "agent-memory", "scope": "agent"})
    )
    try:
        assert isinstance(bootstrap.runtime._config.memory_provider, InMemoryMemoryProvider)
        assert bootstrap.runtime.capabilities().memory is True
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_unresolvable_credential_refs_fail_before_runtime_creation(monkeypatch):
    monkeypatch.delenv("external-token", raising=False)
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {"model": {"ref": "fake-model", "provider": "fake"}},
                "security": {"credential_refs": ["external-token"]},
            },
        }
    )
    with pytest.raises(BootstrapError, match="Required credentials are not available"):
        build_runtime(definition)


def test_resolvable_credential_refs_pass_validation(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    monkeypatch.setenv("EXTERNAL_TOKEN", "token-value")
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {"model": {"ref": "fake-model", "provider": "fake"}},
                "security": {"credential_refs": ["EXTERNAL_TOKEN"]},
            },
        }
    )
    bootstrap = build_runtime(definition)
    try:
        assert isinstance(bootstrap.runtime, GoogleAdkRuntime)
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


@pytest.mark.asyncio
async def test_non_environment_credential_provider_resolves_references(monkeypatch):
    monkeypatch.delenv("vault-token", raising=False)
    from micro_agent.security import StaticCredentialProvider

    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {
                        "ref": "reasoning-model",
                        "model_id": "gpt-4o-mini",
                        "provider": "openai-compatible",
                        "endpoint": "https://llm.example.test/v1",
                        "credential_ref": "vault-token",
                    }
                },
                "security": {"credential_refs": ["vault-token"]},
            },
        }
    )
    bootstrap = build_runtime(
        definition,
        credential_provider=StaticCredentialProvider({"vault-token": "s3cret-value"}),
    )
    try:
        provider = bootstrap.runtime._model_provider
        assert isinstance(provider, OpenAICompatProvider)
        assert provider._config.api_key == "s3cret-value"
        assert bootstrap.resolved.model_api_key == "s3cret-value"
    finally:
        await bootstrap.runtime.close()


def test_declared_mcp_servers_construct_connection_manager(monkeypatch):
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "profile-services",
                            "transport": "streamable-http",
                            "endpoint": "https://mcp.profile.example.test",
                        }
                    ],
                },
            },
        }
    )
    bootstrap = build_runtime(definition)
    try:
        manager = bootstrap.runtime._config.mcp_manager
        assert manager is not None
        assert bootstrap.runtime.capabilities().mcp is True
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


@pytest.mark.asyncio
async def test_mcp_manager_without_wire_client_fails_startup_non_ready(monkeypatch):
    from micro_agent.mcp import McpConnectionManager

    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "profile-services",
                            "transport": "streamable-http",
                            "endpoint": "https://mcp.profile.example.test",
                        }
                    ],
                },
            },
        }
    )
    # An injected factory-less manager isolates the no-wire-client path from
    # the installed SDK; a deployment that removes the mcp extra gets this
    # same clear startup failure from the bootstrap-constructed manager.
    bootstrap = build_runtime(definition, mcp_manager=McpConnectionManager())
    agent = DefaultMicroAgent(definition, bootstrap.runtime)
    try:
        await agent.initialize()
        with pytest.raises(Exception, match="MCP client factory"):
            await agent.start()
    finally:
        await agent.shutdown()
        await bootstrap.runtime.close()


@pytest.mark.asyncio
async def test_bootstrap_constructs_sdk_backed_mcp_manager():

    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "profile-services",
                            "transport": "streamable-http",
                            "endpoint": "https://mcp.profile.example.test",
                        }
                    ],
                },
            },
        }
    )
    bootstrap = build_runtime(definition)
    try:
        manager = bootstrap.runtime._config.mcp_manager
        assert manager is not None
        # The manager connects real SDK clients; the endpoint above is a
        # placeholder, so startup must still fail non-ready, not hang.
        agent = DefaultMicroAgent(definition, bootstrap.runtime)
        await agent.initialize()
        with pytest.raises(Exception):  # noqa: B017 - unreachable server
            await agent.start()
    finally:
        await bootstrap.runtime.close()


def test_google_adk_runtime_maps_declared_mcp_servers(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "mcp_servers": [
                        {
                            "ref": "profile-services",
                            "transport": "streamable-http",
                            "endpoint": "http://127.0.0.1:9000",
                        }
                    ],
                },
            },
        }
    )
    bootstrap = build_runtime(definition)
    try:
        assert bootstrap.runtime._config.mcp_manager is not None
        assert bootstrap.runtime.capabilities().mcp is True
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_unresolved_native_tools_fail_before_runtime_creation():
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "tools": [{"name": "check_eligibility", "source": "native"}],
                },
            },
        }
    )
    with pytest.raises(BootstrapError, match="Cannot resolve declared native tools"):
        build_runtime(definition)


def test_mcp_sourced_tools_require_declared_mcp_servers():
    definition = load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "tools": [{"name": "profile-lookup", "source": "mcp"}],
                },
            },
        }
    )
    with pytest.raises(BootstrapError, match="no MCP servers are declared"):
        build_runtime(definition)


def test_injected_policy_is_passed_to_the_selected_runtime():
    policy = AgentPolicy(denied_tools=["unrelated"])
    bootstrap = build_runtime(_definition(provider="fake"), policy=policy)
    try:
        assert bootstrap.runtime._config.policy is policy
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_telemetry_is_constructed_with_configured_log_level():
    bootstrap = build_runtime(_definition(provider="fake"))
    try:
        import logging

        assert bootstrap.runtime._config.telemetry is not None
        assert (
            bootstrap.runtime._config.telemetry.logger._logger.level
            == logging.getLogger("micro_agent").level
        )
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def _policy_definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "policy-bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {"model": {"ref": "fake-model", "provider": "fake"}},
                "security": {"policy_refs": ["access-policy"]},
            },
        }
    )


def test_policy_refs_resolve_through_configured_resolver():
    seen_refs: list[list[str]] = []

    def resolver(refs: list[str]):
        seen_refs.append(refs)
        return AgentPolicy(denied_tools=["echo"])

    bootstrap = build_runtime(_policy_definition(), policy_resolver=resolver)
    try:
        assert seen_refs == [["access-policy"]]
        assert bootstrap.runtime._config.policy is not None
        assert bootstrap.runtime._config.policy.denied_tools == ["echo"]
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_unresolved_policy_refs_fail_before_runtime_creation():
    with pytest.raises(BootstrapError, match="Policy references cannot be resolved"):
        build_runtime(_policy_definition())


def test_injected_policy_takes_precedence_over_resolver():
    injected = AgentPolicy(denied_tools=["echo"])

    def resolver(refs: list[str]):
        raise AssertionError("resolver must not run when a policy is injected")

    bootstrap = build_runtime(_policy_definition(), policy=injected, policy_resolver=resolver)
    try:
        assert bootstrap.runtime._config.policy is injected
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def _knowledge_definition():
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "knowledge-bootstrap-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Bootstrap test agent."},
                "dependencies": {
                    "model": {"ref": "fake-model", "provider": "fake"},
                    "knowledge": [{"ref": "residency-rules", "source_type": "document"}],
                },
            },
        }
    )


def test_declared_knowledge_constructs_builtin_provider():
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    bootstrap = build_runtime(_knowledge_definition())
    try:
        assert isinstance(bootstrap.runtime._config.knowledge_provider, InMemoryKnowledgeRetriever)
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


def test_declared_knowledge_constructs_provider_for_google_adk(monkeypatch):
    monkeypatch.setenv("MICRO_AGENT_RUNTIME", "google-adk")
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    bootstrap = build_runtime(_knowledge_definition())
    try:
        assert isinstance(bootstrap.runtime._config.knowledge_provider, InMemoryKnowledgeRetriever)
    finally:
        import asyncio

        asyncio.run(bootstrap.runtime.close())


@pytest.mark.asyncio
async def test_unavailable_knowledge_source_fails_startup():
    agent = DefaultMicroAgent(
        _knowledge_definition(), build_runtime(_knowledge_definition()).runtime
    )
    try:
        await agent.initialize()
        with pytest.raises(RuntimeError, match="knowledge source 'residency-rules'"):
            await agent.start()
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_available_knowledge_source_passes_startup():
    from micro_agent.knowledge import InMemoryKnowledgeRetriever

    retriever = InMemoryKnowledgeRetriever(documents={"residency-rules": ["Rule one."]})
    bootstrap = build_runtime(_knowledge_definition(), knowledge_retriever=retriever)
    agent = DefaultMicroAgent(_knowledge_definition(), bootstrap.runtime)
    try:
        await agent.initialize()
        await agent.start()
        assert "knowledge" in bootstrap.runtime.health_probes()
    finally:
        await agent.stop()
        await agent.shutdown()
        await bootstrap.runtime.close()
