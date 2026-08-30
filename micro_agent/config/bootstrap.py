"""Executable runtime bootstrap.

The bootstrap translates a portable definition and environment bindings into
the provider objects consumed by the current runtime.  It deliberately keeps
provider selection small and explicit: an endpoint (or an OpenAI-compatible
provider name) selects :class:`OpenAICompatProvider`; ``fake`` selects the
deterministic test provider; an unsupported or incomplete live configuration
fails before the service is started.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

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
from micro_agent.session import (
    InMemorySessionProvider,
    SessionProvider,
    SqliteSessionProvider,
)
from runtimes.adk import AdkRuntime, AdkRuntimeConfig


class BootstrapError(RuntimeError):
    """Raised when executable runtime configuration cannot be resolved."""


@dataclass(frozen=True)
class RuntimeBootstrap:
    """Runtime and resolved configuration produced by :func:`build_runtime`.

    ``resolved`` is an internal bootstrap result and may contain a secret.
    Callers must not serialize it or include it in logs, responses, or errors.
    """

    runtime: AdkRuntime
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
    named environment variable.  No provider health check is performed here;
    :meth:`AdkRuntime.start` performs that check before readiness.
    """

    resolved = _resolve_definition_config(definition)
    provider = _build_model_provider(resolved, fake_model_config=fake_model_config)
    session_provider = _build_session_provider(definition, resolved)
    memory_provider, memory_policy = _build_memory_provider(definition, resolved)
    runtime = AdkRuntime(
        AdkRuntimeConfig(
            model_provider=provider,
            session_provider=session_provider,
            memory_provider=memory_provider,
            memory_policy=memory_policy,
            telemetry=telemetry,
        )
    )
    return RuntimeBootstrap(runtime=runtime, resolved=resolved)


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


def _build_model_provider(
    config: ResolvedConfig,
    *,
    fake_model_config: FakeModelConfig | None = None,
) -> ModelProvider:
    provider_name = (config.model_provider or "").strip().lower()
    endpoint = config.model_endpoint

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


def _build_session_provider(
    definition: MicroAgentDefinition, config: ResolvedConfig
) -> SessionProvider | None:
    """Construct the configured development session provider.

    ``memory`` and ``sqlite`` are intentionally the only local providers
    available from the executable bootstrap.  External URLs fail fast until a
    production provider is explicitly implemented, rather than being accepted
    and silently ignored.
    """
    session = definition.spec.dependencies.session
    endpoint = (config.session_endpoint or "").strip()
    if endpoint:
        return _session_provider_from_endpoint(endpoint, session.ttl_seconds)
    if session.persistence == "none":
        return None
    if session.persistence == "memory":
        return InMemorySessionProvider(ttl_seconds=session.ttl_seconds)
    if session.persistence == "sqlite":
        raise BootstrapError(
            "SQLite session persistence requires MICRO_AGENT_SESSION_ENDPOINT with a sqlite:// URL"
        )
    raise BootstrapError(
        "External session persistence requires a supported MICRO_AGENT_SESSION_ENDPOINT provider"
    )


def _session_provider_from_endpoint(endpoint: str, ttl_seconds: int | None) -> SessionProvider:
    """Resolve a session endpoint into a safe local provider."""
    normalized = endpoint.lower()
    if normalized in {"memory", "memory://", "inmemory", "inmemory://"}:
        return InMemorySessionProvider(ttl_seconds=ttl_seconds)
    if not normalized.startswith("sqlite://"):
        raise BootstrapError("Unsupported session endpoint; use memory:// or sqlite:///path")

    parsed = urlsplit(endpoint)
    if parsed.query or parsed.fragment:
        raise BootstrapError("SQLite session endpoint must not include query or fragment")
    path = unquote(parsed.path)
    if parsed.netloc and path:
        raise BootstrapError("SQLite session endpoint must use a local path")
    if parsed.netloc:
        path = unquote(parsed.netloc)
    if path in {"", "/"}:
        raise BootstrapError("SQLite session endpoint requires a database path")
    if path == "/:memory:":
        path = ":memory:"
    return SqliteSessionProvider(path, ttl_seconds=ttl_seconds)


def _build_memory_provider(
    definition: MicroAgentDefinition, config: ResolvedConfig
) -> tuple[MemoryProvider | None, MemoryPolicy | None]:
    """Construct the configured in-memory provider when requested."""
    memory = definition.spec.dependencies.memory
    endpoint = (config.memory_endpoint or "").strip()
    if endpoint:
        normalized = endpoint.lower()
        if normalized not in {"memory", "memory://", "inmemory", "inmemory://"}:
            raise BootstrapError(
                "Unsupported memory endpoint; only memory:// is available in the bootstrap"
            )
        return InMemoryMemoryProvider(), MemoryPolicy()
    if memory is None:
        return None, None
    if memory.ref.lower() in {"memory", "inmemory", "in-memory"}:
        return InMemoryMemoryProvider(), MemoryPolicy()
    raise BootstrapError(
        f"Memory provider '{memory.ref}' is not configured; set "
        "MICRO_AGENT_MEMORY_ENDPOINT=memory:// for the local provider"
    )


__all__ = ["BootstrapError", "RuntimeBootstrap", "build_runtime"]
