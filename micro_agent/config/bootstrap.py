"""Executable runtime bootstrap.

The bootstrap translates a portable definition and environment bindings into
provider objects consumed by the selected runtime. Provider and runtime
selection are explicit: an endpoint (or an OpenAI-compatible provider name)
selects :class:`OpenAICompatProvider`; ``fake`` selects the deterministic test
provider; ``MICRO_AGENT_RUNTIME=google-adk`` selects the optional Google ADK
adapter; unsupported or incomplete configurations fail before service start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from micro_agent.config.config import (
    EnvironmentConfig,
    ResolvedConfig,
    SecretRef,
    resolve_config,
    validate_config,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.memory import InMemoryMemoryProvider, MemoryPolicy, MemoryProvider
from micro_agent.models import (
    FakeModelConfig,
    FakeModelProvider,
    ModelProvider,
    OpenAICompatConfig,
    OpenAICompatProvider,
)
from micro_agent.observability import Telemetry
from micro_agent.runtime import AgentRuntime
from micro_agent.session import InMemorySessionProvider, SessionProvider, SqliteSessionProvider
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
) -> RuntimeBootstrap:
    """Build the current runtime from a definition and environment.

    Definition model fields provide the base values. ``MICRO_AGENT_*`` values
    override them, while a definition ``credential_ref`` resolves from the
    named environment variable. No provider health check is performed here;
    the selected runtime performs its startup checks before readiness.
    """

    resolved = _resolve_definition_config(definition)
    runtime_name = _runtime_name(resolved.runtime)
    provider = _build_model_provider(
        resolved,
        fake_model_config=fake_model_config,
        allow_native_google=runtime_name == "google-adk",
    )
    if runtime_name == "google-adk":
        _validate_google_adk_bindings(definition, resolved)
        from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

        runtime: AgentRuntime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
    else:
        session_provider = _build_session_provider(definition, resolved)
        memory_provider = _build_memory_provider(definition, resolved)
        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=provider,
                session_provider=session_provider,
                memory_provider=memory_provider,
                memory_policy=MemoryPolicy() if memory_provider is not None else None,
                telemetry=telemetry,
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
    """Reject declarations the optional adapter cannot silently ignore."""
    dependencies = definition.spec.dependencies
    if (
        dependencies.session.persistence != "none"
        or dependencies.memory is not None
        or config.session_endpoint is not None
        or config.memory_endpoint is not None
    ):
        raise BootstrapError(
            "Google ADK runtime does not yet map Micro-Agent memory/session providers; "
            "use the custom runtime or remove the state declarations"
        )
    if dependencies.mcp_servers or config.mcp_endpoints:
        raise BootstrapError(
            "Google ADK runtime does not yet map declared MCP servers; "
            "use the custom runtime or remove the MCP declarations"
        )
    if definition.spec.security.policy_refs:
        raise BootstrapError(
            "Google ADK runtime does not yet map policy references; "
            "use the custom runtime or remove the policy declarations"
        )
    if dependencies.knowledge:
        raise BootstrapError(
            "Google ADK runtime does not yet map knowledge providers; "
            "use the custom runtime or remove the knowledge declarations"
        )
    model = dependencies.model
    if model is not None and model.credential_ref:
        raise BootstrapError(
            "Google ADK runtime does not yet map model credential references; "
            "use the custom runtime or remove the credential reference"
        )
    if definition.spec.security.credential_refs:
        raise BootstrapError(
            "Google ADK runtime does not yet map security credential references; "
            "use the custom runtime or remove the credential declarations"
        )
    unresolved_tools = [tool.name for tool in dependencies.tools if tool.name != "echo"]
    if unresolved_tools:
        names = ", ".join(unresolved_tools)
        raise BootstrapError(
            "Google ADK runtime cannot resolve declared tools yet: "
            f"{names}; use the custom runtime or remove them"
        )


def _resolve_definition_config(definition: MicroAgentDefinition) -> ResolvedConfig:
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

    resolved = resolve_config(
        definition_overrides=overrides,
        env_config=EnvironmentConfig(model_api_key_ref=credential_ref),
    )
    diagnostics = validate_config(resolved)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.level == "error"]
    if errors:
        detail = "; ".join(diagnostic.message for diagnostic in errors)
        raise BootstrapError(f"Invalid runtime configuration: {detail}")
    if credential_ref is not None and resolved.model_api_key is None:
        raise BootstrapError(f"Required model credential '{credential_ref.name}' is not available")
    return resolved


def _build_session_provider(
    definition: MicroAgentDefinition, config: ResolvedConfig
) -> SessionProvider | None:
    """Construct the configured session provider, or fail before readiness.

    ``memory`` and ``sqlite`` are the providers included in this distribution.
    ``external`` is intentionally rejected until a deployment supplies an
    external provider implementation; silently ignoring that declaration would
    make a supposedly persistent agent lose state.
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
    raise BootstrapError(
        "session.persistence 'external' is declared, but no external session provider is configured"
    )


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

    if endpoint and endpoint not in {"memory://", "inmemory://"}:
        raise BootstrapError(
            "memory dependency requires memory:// or inmemory://; external memory "
            "providers are not configured"
        )
    return InMemoryMemoryProvider(MemoryPolicy())


def _build_model_provider(
    config: ResolvedConfig,
    *,
    fake_model_config: FakeModelConfig | None = None,
    allow_native_google: bool = False,
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


__all__ = ["BootstrapError", "RuntimeBootstrap", "build_runtime"]
