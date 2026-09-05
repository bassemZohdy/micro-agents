"""Executable runtime bootstrap.

The bootstrap translates a portable definition and environment bindings into
provider objects consumed by the selected runtime. Provider and runtime
selection are explicit: an endpoint (or an OpenAI-compatible provider name)
selects :class:`OpenAICompatProvider`; ``fake`` selects the deterministic test
provider; ``MICRO_AGENT_RUNTIME=google-adk`` selects the optional Google ADK
adapter; unsupported or incomplete configurations fail before service start.

Constructed from configuration:

- the built-in native tool registry for the definition's declared tools,
- an MCP connection manager for declared MCP servers (a deployment may inject
  a manager that owns a real wire-protocol client factory),
- telemetry with the configured log level,
- built-in memory, optional Redis memory, and in-memory/SQLite session providers,
  or an optional Redis external session provider,
- an optional Redis operation registry for distributed idempotency,
- a knowledge provider for declared knowledge sources (injected, or the
  built-in in-memory retriever whose startup health check fails fast until a
  deployment supplies documents),
- a credential provider (injected non-environment provider, or the built-in
  environment provider); every declared credential reference must resolve,
- the platform policy, from an injected policy or a policy resolver for the
  declared policy references; unresolved policy references fail fast.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from micro_agent.checkpoint import SessionCheckpointStore
from micro_agent.config.config import (
    EnvironmentConfig,
    EnvironmentOverlay,
    ResolvedConfig,
    SecretRef,
    resolve_config,
    validate_config,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.knowledge import InMemoryKnowledgeRetriever, KnowledgeRetriever
from micro_agent.mcp import McpConnectionManager
from micro_agent.memory import (
    InMemoryMemoryProvider,
    MemoryPolicy,
    MemoryProvider,
    RedisMemoryProvider,
)
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelProvider,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from micro_agent.observability import FileAuditSink, JsonlAuditSink, NullAuditSink, Telemetry
from micro_agent.observability.audit import AuditSink
from micro_agent.runtime import AgentRuntime
from micro_agent.security import (
    AgentPolicy,
    ApprovalStore,
    CredentialProvider,
    EnvironmentCredentialProvider,
    OperationRegistryProtocol,
    RedisApprovalStore,
    RedisOperationRegistry,
)
from micro_agent.security.auth import Authenticator, OidcJwtAuthenticator
from micro_agent.session import (
    InMemorySessionProvider,
    RedisSessionProvider,
    SessionProvider,
    SqliteSessionProvider,
)
from micro_agent.tools import Tool, builtin_tool_registry
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


class BootstrapError(RuntimeError):
    """Raised when executable runtime configuration cannot be resolved."""


@dataclass(frozen=True)
class RuntimeBootstrap:
    """Runtime and resolved configuration produced by :func:`build_runtime`.

    ``resolved`` is an internal bootstrap result and may contain a secret.
    Callers must not serialize it or include it in logs, responses, or errors.
    """

    runtime: AgentRuntime
    resolved: ResolvedConfig


_OPENAI_PROVIDER_NAMES = {
    "openai",
    "openai-compatible",
    "openai_compatible",
    "openai-compat",
    "openai_compat",
}
_FAKE_PROVIDER_NAMES = {"fake", "test", "stub"}


def build_runtime(
    definition: MicroAgentDefinition,
    *,
    telemetry: Telemetry | None = None,
    fake_model_config: FakeModelConfig | None = None,
    policy: AgentPolicy | None = None,
    policy_resolver: Callable[[list[str]], AgentPolicy | None] | None = None,
    mcp_manager: McpConnectionManager | None = None,
    credential_provider: CredentialProvider | None = None,
    knowledge_retriever: KnowledgeRetriever | None = None,
    environment: EnvironmentConfig | EnvironmentOverlay | None = None,
    approval_store: ApprovalStore | None = None,
) -> RuntimeBootstrap:
    """Build the current runtime from a definition and environment.

    Definition model fields provide the base values. ``MICRO_AGENT_*`` values
    override them, while a definition ``credential_ref`` resolves through the
    configured credential provider (environment by default). The tool
    registry, MCP client, memory/session providers, knowledge provider,
    policy, and telemetry are constructed from the definition and
    configuration; declarations that cannot be satisfied fail before runtime
    creation. No provider health check is performed here; the selected
    runtime performs its startup checks before readiness.
    """

    credential_provider = credential_provider or EnvironmentCredentialProvider()
    resolved = _resolve_definition_config(definition, credential_provider, environment)
    runtime_name = _runtime_name(resolved.runtime)

    telemetry = telemetry or Telemetry.from_environment()
    telemetry.logger.set_level(resolved.log_level)

    _validate_credential_bindings(definition, credential_provider)
    mcp = _build_mcp_manager(
        definition,
        mcp_manager,
        credential_provider,
        endpoint_overrides=resolved.mcp_endpoints,
        telemetry=telemetry,
    )
    tool_registry = _build_tool_registry(definition)
    _validate_tool_bindings(definition, tool_registry, mcp)
    effective_policy = _resolve_policy(definition, policy, policy_resolver)
    knowledge_provider = _build_knowledge_provider(definition, knowledge_retriever)
    audit_sink = build_audit_sink(resolved)

    provider = _build_model_provider(
        resolved,
        fake_model_config=fake_model_config,
        allow_native_google=runtime_name == "google-adk",
        telemetry=telemetry,
    )
    if runtime_name == "google-adk":
        _validate_google_adk_bindings(definition, resolved)
        memory_provider = _build_memory_provider(definition, resolved)
        from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

        runtime: AgentRuntime = GoogleAdkRuntime(
            GoogleAdkRuntimeConfig(
                model_provider=provider,
                model_api_key=resolved.model_api_key,
                memory_provider=memory_provider,
                memory_policy=MemoryPolicy() if memory_provider is not None else None,
                knowledge_provider=knowledge_provider,
                mcp_manager=mcp,
                policy=effective_policy,
                audit=audit_sink,
                telemetry=telemetry,
                tool_registry=tool_registry,
            )
        )
    else:
        session_provider = _build_session_provider(definition, resolved)
        checkpoint_store = (
            SessionCheckpointStore(
                session_provider,
                ttl_seconds=definition.spec.dependencies.session.ttl_seconds,
            )
            if session_provider is not None
            else None
        )
        memory_provider = _build_memory_provider(definition, resolved)
        operation_registry = _build_operation_registry(resolved)
        effective_approval_store = approval_store or _build_approval_store(resolved)
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=provider,
                session_provider=session_provider,
                checkpoint_store=checkpoint_store,
                memory_provider=memory_provider,
                memory_policy=MemoryPolicy() if memory_provider is not None else None,
                operation_registry=operation_registry,
                approval_store=effective_approval_store,
                knowledge_provider=knowledge_provider,
                mcp_manager=mcp,
                policy=effective_policy,
                audit=audit_sink,
                telemetry=telemetry,
                tool_registry=tool_registry,
            )
        )
    return RuntimeBootstrap(runtime=runtime, resolved=resolved)


_RUNTIME_ALIASES = {
    "adk": "custom",
    "custom": "custom",
    "reference": "custom",
    "google-adk": "google-adk",
    "google_adk": "google-adk",
    "googleadk": "google-adk",
}


def _runtime_name(value: str | None) -> str:
    """Normalize the deployment-selected runtime name."""
    normalized = (value or "custom").strip().lower()
    try:
        return _RUNTIME_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(set(_RUNTIME_ALIASES.values())))
        raise BootstrapError(
            f"Unsupported runtime '{value}'. Supported runtimes: {supported}"
        ) from exc


def _validate_google_adk_bindings(definition: MicroAgentDefinition, config: ResolvedConfig) -> None:
    """Reject declarations the optional adapter cannot map to ADK constructs."""
    session = definition.spec.dependencies.session
    endpoint = config.session_endpoint
    if session.persistence not in {"none", "memory"} or (
        endpoint and endpoint not in {"memory://", "inmemory://"}
    ):
        raise BootstrapError(
            "Google ADK runtime maps only in-memory sessions; use session "
            "persistence 'none' or 'memory' with a memory:// endpoint, or use "
            "the custom runtime for sqlite/external state"
        )
    if config.idempotency_endpoint:
        raise BootstrapError(
            "Google ADK runtime does not yet map distributed idempotency; use "
            "the custom runtime for MICRO_AGENT_IDEMPOTENCY_ENDPOINT"
        )
    if config.approval_endpoint:
        raise BootstrapError(
            "Google ADK runtime uses native approval continuations and does not map "
            "MICRO_AGENT_APPROVAL_ENDPOINT; use the custom runtime for durable approvals"
        )


def _resolve_definition_config(
    definition: MicroAgentDefinition,
    credential_provider: CredentialProvider,
    environment: EnvironmentConfig | EnvironmentOverlay | None = None,
) -> ResolvedConfig:
    model = definition.spec.dependencies.model
    overrides: dict[str, Any] = {}
    credential_ref: SecretRef | None = None
    if model is not None:
        overrides = {
            "model_ref": model.ref,
            "model_id": model.model_id,
            "model_provider": model.provider,
            "model_endpoint": model.endpoint,
            "model_generation": dict(model.generation),
            "model_timeout_seconds": model.timeout_seconds,
        }
        if model.credential_ref:
            credential_ref = SecretRef(name=model.credential_ref, source="env")

    environment_config: EnvironmentConfig | None
    if isinstance(environment, EnvironmentOverlay):
        environment_config = environment.to_environment_config()
    else:
        environment_config = environment
    if credential_ref is not None and (
        environment_config is None or environment_config.model_api_key_ref is None
    ):
        if environment_config is None:
            environment_config = EnvironmentConfig(model_api_key_ref=credential_ref)
        else:
            environment_config = environment_config.model_copy(
                update={"model_api_key_ref": credential_ref}
            )
    resolved = resolve_config(
        definition_overrides=overrides,
        env_config=environment_config,
    )
    diagnostics = validate_config(resolved)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.level == "error"]
    if errors:
        detail = "; ".join(diagnostic.message for diagnostic in errors)
        raise BootstrapError(f"Invalid runtime configuration: {detail}")
    if credential_ref is not None and resolved.model_api_key is None:
        # The environment is resolved first (configuration precedence); a
        # configured non-environment provider is the fallback binding.
        resolved.model_api_key = credential_provider.resolve(credential_ref.name)
    if credential_ref is not None and resolved.model_api_key is None:
        raise BootstrapError(f"Required model credential '{credential_ref.name}' is not available")
    return resolved


def _validate_credential_bindings(
    definition: MicroAgentDefinition,
    credential_provider: CredentialProvider,
) -> None:
    """Every declared credential reference must resolve before runtime creation."""
    required: list[str] = []
    model = definition.spec.dependencies.model
    if model is not None and model.credential_ref:
        required.append(model.credential_ref)
    required.extend(
        server.credential_ref
        for server in definition.spec.dependencies.mcp_servers
        if server.credential_ref
    )
    required.extend(definition.spec.security.credential_refs)
    missing = sorted({ref for ref in required if credential_provider.resolve(ref) is None})
    if missing:
        raise BootstrapError(f"Required credentials are not available: {', '.join(missing)}")


def _resolve_policy(
    definition: MicroAgentDefinition,
    injected: AgentPolicy | None,
    resolver: Callable[[list[str]], AgentPolicy | None] | None,
) -> AgentPolicy | None:
    """Resolve the effective platform policy from injection or policy refs.

    Declared policy references must resolve to a policy — through the
    injected policy or a configured resolver — or startup fails; silently
    running without the declared policy would overstate allowed autonomy.
    """
    policy_refs = list(definition.spec.security.policy_refs)
    if not policy_refs:
        return injected
    if injected is not None:
        return injected
    if resolver is not None:
        resolved = resolver(policy_refs)
        if resolved is not None:
            return resolved
    raise BootstrapError(
        f"Policy references cannot be resolved: {', '.join(policy_refs)}; "
        "inject a policy or a policy resolver through the bootstrap"
    )


def _build_session_provider(
    definition: MicroAgentDefinition, config: ResolvedConfig
) -> SessionProvider | None:
    """Construct the configured session provider, or fail before readiness.

    ``memory`` and ``sqlite`` are the providers included in the base
    distribution. ``external`` accepts a Redis endpoint when the optional
    ``redis`` extra is installed; unsupported endpoints fail before runtime
    creation rather than silently falling back to local state.
    """
    session = definition.spec.dependencies.session
    endpoint = config.session_endpoint

    if session.persistence == "none":
        if endpoint:
            raise BootstrapError(
                "MICRO_AGENT_SESSION_ENDPOINT is set but session.persistence is 'none'"
            )
        return None

    if session.persistence == "memory":
        if endpoint and endpoint not in {"memory://", "inmemory://"}:
            raise BootstrapError(
                "session.persistence 'memory' only supports memory:// or inmemory:// "
                "session endpoints"
            )
        return InMemorySessionProvider(ttl_seconds=session.ttl_seconds)

    if session.persistence == "sqlite":
        return SqliteSessionProvider(
            path=_sqlite_path(endpoint),
            ttl_seconds=session.ttl_seconds,
        )

    # The definition model constrains this value, but keep the branch explicit
    # so a future persistence mode cannot silently fall through.
    if not endpoint:
        raise BootstrapError("session.persistence 'external' requires MICRO_AGENT_SESSION_ENDPOINT")
    if endpoint.startswith(("redis://", "rediss://")):
        try:
            return RedisSessionProvider(
                endpoint=endpoint,
                ttl_seconds=session.ttl_seconds,
            )
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    if _is_postgres_endpoint(endpoint):
        from micro_agent.session.postgres import PostgresSessionProvider

        try:
            return PostgresSessionProvider(endpoint, ttl_seconds=session.ttl_seconds)
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    raise BootstrapError(
        "external session provider supports redis://, rediss://, postgres://, or "
        "postgresql:// endpoints; install the 'redis' or 'postgres' extra and "
        "configure MICRO_AGENT_SESSION_ENDPOINT"
    )


def _is_postgres_endpoint(endpoint: str) -> bool:
    return endpoint.startswith(("postgres://", "postgresql://"))


def _sqlite_path(endpoint: str | None) -> str:
    """Normalize a SQLite endpoint into a path understood by sqlite3."""
    if not endpoint:
        # Keep the built-in provider useful for local development while making
        # it clear in documentation that this is process-local state.
        return ":memory:"
    if endpoint in {":memory:", "sqlite:///:memory:", "sqlite://:memory:"}:
        return ":memory:"
    if endpoint.startswith("sqlite://"):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "sqlite" or parsed.query or parsed.fragment:
            raise BootstrapError("SQLite session endpoint must not include query or fragment")
        if parsed.netloc and parsed.path:
            # sqlite://host/path is ambiguous and could accidentally target a
            # remote-looking location; require the conventional file form.
            raise BootstrapError(
                "SQLite session endpoint must use sqlite:///absolute/path or sqlite:///:memory:"
            )
        path = parsed.path
        if not path:
            raise BootstrapError("SQLite session endpoint must include a database path")
        return path
    if endpoint.startswith("file:"):
        return endpoint
    if "://" in endpoint:
        raise BootstrapError("SQLite session endpoint must use the sqlite:// scheme")
    return endpoint


def _build_memory_provider(
    definition: MicroAgentDefinition, config: ResolvedConfig
) -> MemoryProvider | None:
    """Construct the built-in memory provider for a declared memory dependency."""
    memory = definition.spec.dependencies.memory
    endpoint = config.memory_endpoint
    if memory is None:
        if endpoint:
            raise BootstrapError(
                "MICRO_AGENT_MEMORY_ENDPOINT is set but no memory dependency is declared"
            )
        return None

    if not endpoint or endpoint in {"memory://", "inmemory://"}:
        return InMemoryMemoryProvider(MemoryPolicy())
    if endpoint.startswith(("redis://", "rediss://")):
        try:
            return RedisMemoryProvider(endpoint=endpoint, policy=MemoryPolicy())
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    if _is_postgres_endpoint(endpoint):
        from micro_agent.memory.postgres import PostgresMemoryProvider

        try:
            return PostgresMemoryProvider(endpoint, policy=MemoryPolicy())
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    raise BootstrapError(
        "external memory provider supports redis://, rediss://, postgres://, or "
        "postgresql:// endpoints; install the 'redis' or 'postgres' extra and "
        "configure MICRO_AGENT_MEMORY_ENDPOINT"
    )


def _build_operation_registry(config: ResolvedConfig) -> OperationRegistryProtocol | None:
    """Construct the optional distributed idempotency registry."""
    endpoint = config.idempotency_endpoint
    if not endpoint:
        return None
    if endpoint.startswith(("redis://", "rediss://")):
        try:
            return RedisOperationRegistry(endpoint=endpoint)
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    if _is_postgres_endpoint(endpoint):
        from micro_agent.memory.postgres import PostgresIdempotencyStore

        try:
            return PostgresIdempotencyStore(endpoint)
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    raise BootstrapError(
        "external idempotency provider supports redis://, rediss://, postgres://, or "
        "postgresql:// endpoints; install the 'redis' or 'postgres' extra and "
        "configure MICRO_AGENT_IDEMPOTENCY_ENDPOINT"
    )


def _build_approval_store(config: ResolvedConfig) -> ApprovalStore | None:
    """Construct the optional durable approval continuation store."""
    endpoint = config.approval_endpoint
    if not endpoint:
        return None
    if endpoint.startswith(("redis://", "rediss://")):
        try:
            return RedisApprovalStore(endpoint=endpoint)
        except (RuntimeError, ValueError) as exc:
            raise BootstrapError(str(exc)) from exc
    raise BootstrapError(
        "external approval provider supports redis:// or rediss:// endpoints; "
        "install the 'redis' extra and configure MICRO_AGENT_APPROVAL_ENDPOINT"
    )


def _build_mcp_manager(
    definition: MicroAgentDefinition,
    injected: McpConnectionManager | None,
    credential_provider: CredentialProvider,
    endpoint_overrides: dict[str, str] | None = None,
    telemetry: Telemetry | None = None,
) -> McpConnectionManager | None:
    """Construct the MCP client for declared servers; an injected manager wins.

    The constructed manager applies the built-in MCP security policy,
    resolves declared MCP credentials through the configured credential
    provider, and uses the official MCP SDK wire client when the optional
    ``mcp`` extra is installed; without it, connections fail at startup with
    a clear installation message. Failing non-ready is deliberate: silently
    skipping declared MCP servers would understate the agent's real
    capabilities.
    """
    if not definition.spec.dependencies.mcp_servers:
        if endpoint_overrides:
            unknown_ref_text = ", ".join(sorted(endpoint_overrides))
            raise BootstrapError(
                f"MCP endpoint bindings reference undeclared server(s): {unknown_ref_text}"
            )
        return None
    endpoint_overrides = dict(endpoint_overrides or {})
    declared_refs = {server.ref for server in definition.spec.dependencies.mcp_servers}
    unknown_refs = sorted(set(endpoint_overrides) - declared_refs)
    if unknown_refs:
        raise BootstrapError(
            "MCP endpoint bindings reference undeclared server(s): " + ", ".join(unknown_refs)
        )
    if injected is not None:
        if endpoint_overrides:
            injected.set_endpoint_overrides(endpoint_overrides)
        return injected
    from micro_agent.mcp.sdk_client import sdk_available, sdk_client_factory

    factory = sdk_client_factory(telemetry=telemetry) if sdk_available() else None
    return McpConnectionManager(
        client_factory=factory,
        credential_resolver=credential_provider.resolve,
        endpoint_overrides=endpoint_overrides,
    )


def _build_knowledge_provider(
    definition: MicroAgentDefinition,
    injected: KnowledgeRetriever | None,
) -> KnowledgeRetriever | None:
    """Construct the knowledge provider for declared knowledge sources.

    Without an injected retriever, the built-in in-memory retriever is used;
    it holds no documents, so the startup health check fails fast for any
    declared source until a deployment supplies one.
    """
    if not definition.spec.dependencies.knowledge:
        return None
    if injected is not None:
        return injected
    return InMemoryKnowledgeRetriever()


def _build_tool_registry(definition: MicroAgentDefinition) -> dict[str, Tool]:
    """Instantiate the built-in native tools declared by the definition."""
    from micro_agent.tools.plugin import load_plugin_tools

    builtins = builtin_tool_registry()
    builtins.update(load_plugin_tools())
    return {
        tool_definition.name: builtins[tool_definition.name]
        for tool_definition in definition.spec.dependencies.tools
        if tool_definition.source != "mcp" and tool_definition.name in builtins
    }


def _validate_tool_bindings(
    definition: MicroAgentDefinition,
    registry: dict[str, Tool],
    mcp_manager: McpConnectionManager | None,
) -> None:
    """Reject tool declarations the constructed registries cannot satisfy."""
    unresolved = [
        tool_definition.name
        for tool_definition in definition.spec.dependencies.tools
        if tool_definition.source != "mcp" and tool_definition.name not in registry
    ]
    if unresolved:
        names = ", ".join(unresolved)
        raise BootstrapError(
            f"Cannot resolve declared native tools: {names}; only built-in "
            "native tools and MCP tools can be resolved"
        )
    if mcp_manager is None and any(
        tool_definition.source == "mcp" for tool_definition in definition.spec.dependencies.tools
    ):
        raise BootstrapError("MCP-sourced tools are declared but no MCP servers are declared")


def _build_model_provider(
    config: ResolvedConfig,
    *,
    fake_model_config: FakeModelConfig | None = None,
    allow_native_google: bool = False,
    telemetry: Telemetry | None = None,
) -> ModelProvider | None:
    provider_name = (config.model_provider or "").strip().lower()
    endpoint = config.model_endpoint

    if provider_name in {"google", "gemini", "google-genai"}:
        if allow_native_google and not endpoint:
            if not config.model_id and not config.model_ref:
                raise BootstrapError(
                    "Google ADK runtime requires model_id or model_ref for a native model"
                )
            return None
        raise BootstrapError(
            "Google model providers require runtime: google-adk without an endpoint"
        )

    if provider_name in _FAKE_PROVIDER_NAMES:
        return FakeModelProvider(fake_model_config)

    # An endpoint is an unambiguous request for a network provider.  This also
    # makes environment-only configuration useful without requiring a provider
    # discriminator.
    if provider_name in _OPENAI_PROVIDER_NAMES or endpoint:
        if not endpoint:
            raise BootstrapError(
                "OpenAI-compatible model provider requires model_endpoint or "
                "MICRO_AGENT_MODEL_ENDPOINT"
            )
        if not config.model_id:
            raise BootstrapError(
                "OpenAI-compatible model provider requires model_id or "
                "MICRO_AGENT_MODEL_ID; model_ref is a logical alias only"
            )
        return OpenAICompatProvider(
            OpenAICompatConfig(
                endpoint=endpoint,
                model_id=config.model_id,
                api_key=config.model_api_key,
                timeout_seconds=float(config.model_timeout_seconds or 30),
                telemetry=telemetry,
            )
        )

    if provider_name:
        supported = ", ".join(sorted(_FAKE_PROVIDER_NAMES | _OPENAI_PROVIDER_NAMES))
        raise BootstrapError(
            f"Unsupported model provider '{config.model_provider}'. "
            f"Supported providers: {supported}"
        )

    raise BootstrapError(
        "Model provider is not configured. Set provider: fake for offline "
        "development or configure an OpenAI-compatible endpoint."
    )


def build_authenticator(config: ResolvedConfig) -> Authenticator | None:
    """Build the transport authenticator from resolved configuration.

    ``MICRO_AGENT_AUTH`` selects the implementation; ``oidc`` (the dominant
    scheme) validates OIDC/OAuth2 Bearer JWTs against the configured issuer
    and audience. Unauthenticated access remains the development default
    until an authenticator is configured.
    """
    auth_mode = (config.auth or "").strip().lower()
    if auth_mode in ("", "none"):
        return None
    if auth_mode == "oidc":
        if not config.auth_issuer or not config.auth_audience:
            raise BootstrapError(
                "MICRO_AGENT_AUTH=oidc requires MICRO_AGENT_AUTH_ISSUER and "
                "MICRO_AGENT_AUTH_AUDIENCE"
            )
        return OidcJwtAuthenticator(issuer=config.auth_issuer, audience=config.auth_audience)
    raise BootstrapError(f"Unsupported auth mode '{config.auth}'. Supported modes: none, oidc")


def build_audit_sink(config: ResolvedConfig) -> AuditSink:
    """Build the audit sink from resolved configuration.

    ``stdout`` (default) emits redacted JSON lines for platform log
    collection; ``file`` appends to ``MICRO_AGENT_AUDIT_FILE``; ``none``
    disables auditing.
    """
    mode = (config.audit_sink or "stdout").strip().lower()
    if mode in ("", "stdout"):
        return JsonlAuditSink()
    if mode == "file":
        if not config.audit_file:
            raise BootstrapError("MICRO_AGENT_AUDIT_SINK=file requires MICRO_AGENT_AUDIT_FILE")
        return FileAuditSink(config.audit_file)
    if mode == "none":
        return NullAuditSink()
    raise BootstrapError(
        f"Unsupported audit sink '{config.audit_sink}'. Supported: none, stdout, file"
    )


__all__ = [
    "BootstrapError",
    "RuntimeBootstrap",
    "build_audit_sink",
    "build_authenticator",
    "build_runtime",
]
