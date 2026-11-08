"""Runtime registry: the pluggable layer between bootstrap and runtimes.

The bootstrap builds every provider a Micro-Agent definition asks for (model,
session, memory, knowledge, MCP, policy, audit, telemetry, tools) and hands them
to the selected runtime as one :class:`RuntimeBuildContext`. A runtime is
selected by name through this registry, so adding a framework adapter means
registering a :class:`RuntimeRegistration`; neither the bootstrap nor the core
changes.

Built-in runtimes (``custom``, ``google-adk``, ``langgraph``) are registered by
the composition root in ``micro_agent.config.runtimes``. Third-party packages
can register through :func:`register_runtime` or the ``micro_agent.runtimes``
entry-point group; an entry point resolves to a :class:`RuntimeRegistration`
(or a zero-argument callable returning one) and is loaded only when selected.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from micro_agent.runtime.runtime import AgentRuntime

if TYPE_CHECKING:
    from micro_agent.definition import MicroAgentDefinition
    from micro_agent.knowledge import KnowledgeRetriever
    from micro_agent.mcp import McpConnectionManager
    from micro_agent.memory import MemoryProvider
    from micro_agent.models import ModelProvider
    from micro_agent.observability import AuditSink, Telemetry
    from micro_agent.security import AgentPolicy, ApprovalStore, OperationRegistryProtocol
    from micro_agent.session import SessionProvider
    from micro_agent.tools import Tool

RUNTIME_ENTRY_POINT_GROUP = "micro_agent.runtimes"


class UnknownRuntimeError(LookupError):
    """Raised when a runtime name is not registered."""


@dataclass(frozen=True)
class RuntimeBuildContext:
    """Everything the bootstrap built for one definition.

    Runtime factories translate this into their own configuration. Only
    Micro-Agent contract types appear here; no framework type crosses it.
    """

    definition: MicroAgentDefinition
    model_provider: ModelProvider | None  # None: the runtime selects a native model
    model_api_key: str | None
    session_provider: SessionProvider | None
    memory_provider: MemoryProvider | None
    operation_registry: OperationRegistryProtocol | None
    approval_store: ApprovalStore | None
    knowledge_provider: KnowledgeRetriever | None
    mcp_manager: McpConnectionManager | None
    policy: AgentPolicy | None
    audit: AuditSink | None
    telemetry: Telemetry
    tool_registry: dict[str, Tool]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def session_ttl_seconds(self) -> int | None:
        return self.definition.spec.dependencies.session.ttl_seconds

    @property
    def session_persistence(self) -> str:
        return self.definition.spec.dependencies.session.persistence


RuntimeFactory = Callable[[RuntimeBuildContext], AgentRuntime]


@dataclass(frozen=True)
class RuntimeRegistration:
    """How the bootstrap selects, validates and builds one runtime.

    ``native_model`` lets the runtime use a framework-native model when no
    endpoint is configured (for example ADK's Gemini path). ``approval_store``
    says whether the runtime consumes the durable ``ApprovalStore``; a runtime
    that does not must explain, in ``approval_note``, why
    ``MICRO_AGENT_APPROVAL_ENDPOINT`` is rejected instead of silently ignored.
    """

    name: str
    factory: RuntimeFactory
    aliases: tuple[str, ...] = ()
    description: str = ""
    native_model: bool = False
    approval_store: bool = False
    approval_note: str = ""

    def __post_init__(self) -> None:
        if not _normalize(self.name):
            raise ValueError("runtime name must not be empty")
        if not callable(self.factory):
            raise TypeError("runtime factory must be callable")

    @property
    def all_names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_normalize(n) for n in (self.name, *self.aliases)))


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


class RuntimeRegistry:
    """Name → registration lookup with lazy entry-point discovery."""

    def __init__(self, *, entry_point_group: str | None = RUNTIME_ENTRY_POINT_GROUP) -> None:
        self._registrations: dict[str, RuntimeRegistration] = {}
        self._aliases: dict[str, str] = {}
        self._entry_point_group = entry_point_group

    def register(self, registration: RuntimeRegistration, *, replace: bool = False) -> None:
        """Register a runtime under its name and aliases."""
        canonical = _normalize(registration.name)
        taken = [
            n
            for n in registration.all_names
            if n in self._aliases and self._aliases[n] != canonical
        ]
        if taken:
            raise ValueError(f"runtime name already registered: {', '.join(sorted(taken))}")
        if canonical in self._registrations and not replace:
            raise ValueError(f"runtime '{canonical}' is already registered")
        self._registrations[canonical] = registration
        for alias in registration.all_names:
            self._aliases[alias] = canonical

    def unregister(self, name: str) -> None:
        """Remove a runtime and its aliases (mainly for tests and hot reload)."""
        canonical = self._aliases.get(_normalize(name))
        if canonical is None:
            raise UnknownRuntimeError(name)
        del self._registrations[canonical]
        self._aliases = {a: c for a, c in self._aliases.items() if c != canonical}

    def names(self) -> list[str]:
        """Canonical names, including entry-point runtimes not yet loaded."""
        names = set(self._registrations)
        for entry_point in self._entry_points():
            names.add(_normalize(entry_point.name))
        return sorted(names)

    def get(self, name: str) -> RuntimeRegistration:
        """Resolve a name or alias; load a matching entry point on demand."""
        key = _normalize(name)
        canonical = self._aliases.get(key)
        if canonical is None:
            self._load_entry_point(key)
            canonical = self._aliases.get(key)
        if canonical is None:
            raise UnknownRuntimeError(name)
        return self._registrations[canonical]

    def _entry_points(self) -> list[importlib.metadata.EntryPoint]:
        if self._entry_point_group is None:
            return []
        return list(importlib.metadata.entry_points(group=self._entry_point_group))

    def _load_entry_point(self, key: str) -> None:
        for entry_point in self._entry_points():
            if _normalize(entry_point.name) != key:
                continue
            loaded = entry_point.load()
            registration = loaded() if not isinstance(loaded, RuntimeRegistration) else loaded
            if not isinstance(registration, RuntimeRegistration):
                raise TypeError(
                    f"runtime entry point '{entry_point.name}' must provide a RuntimeRegistration"
                )
            self.register(registration)
            return


default_registry = RuntimeRegistry()


def register_runtime(registration: RuntimeRegistration, *, replace: bool = False) -> None:
    """Register a runtime with the process-wide default registry."""
    default_registry.register(registration, replace=replace)


def get_runtime_registration(name: str) -> RuntimeRegistration:
    """Resolve a runtime name or alias from the default registry."""
    return default_registry.get(name)


def registered_runtimes() -> list[str]:
    """Canonical runtime names known to the default registry."""
    return default_registry.names()
