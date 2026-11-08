"""Micro-Agent Runtime SPI (Service Provider Interface).

Defines the smallest useful runtime abstraction.
No framework-native types cross the public boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
    AgentStreamEvent,
)
from micro_agent.definition import MicroAgentDefinition
from micro_agent.health import DependencyProbe

RUNTIME_CAPABILITY_NAMES = frozenset(
    {"streaming", "memory", "mcp", "a2a", "structured_output", "checkpointing"}
)

# ---------------------------------------------------------------------------
# Runtime Capabilities
# ---------------------------------------------------------------------------


@dataclass
class RuntimeCapabilities:
    """Capabilities reported by a runtime implementation.

    Each boolean says whether the capability is *effectively* available. A
    capability named in ``provided`` is available only because the
    Micro-Agents gap-filling layer supplies it on top of the framework; every
    other true capability is ``native`` to the runtime. A provided capability
    is always reported as true so existing ``supports()`` callers keep working.
    """

    streaming: bool = False
    memory: bool = False
    mcp: bool = False
    a2a: bool = False
    structured_output: bool = False
    checkpointing: bool = False
    provided: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        self.provided = frozenset(self.provided)
        unknown = self.provided - RUNTIME_CAPABILITY_NAMES
        if unknown:
            raise ValueError(f"unknown provided capabilities: {', '.join(sorted(unknown))}")
        for name in self.provided:
            setattr(self, name, True)

    def supports(self, capability: str) -> bool:
        """Return whether this runtime advertises a named capability."""
        return capability in RUNTIME_CAPABILITY_NAMES and bool(getattr(self, capability, False))

    def source(self, capability: str) -> str:
        """Return ``"native"``, ``"provided"`` or ``"none"`` for a capability."""
        if not self.supports(capability):
            return "none"
        return "provided" if capability in self.provided else "native"

    def with_provided(self, *capabilities: str) -> RuntimeCapabilities:
        """Return a copy where ``capabilities`` are supplied by the gap-filling layer."""
        return replace(self, provided=self.provided | frozenset(capabilities))

    def as_dict(self) -> dict[str, bool]:
        """Return the capability matrix as a serializable mapping."""
        return {
            "streaming": self.streaming,
            "memory": self.memory,
            "mcp": self.mcp,
            "a2a": self.a2a,
            "structured_output": self.structured_output,
            "checkpointing": self.checkpointing,
        }

    def sources(self) -> dict[str, str]:
        """Return each capability's source: ``native``, ``provided`` or ``none``."""
        return {name: self.source(name) for name in self.as_dict()}


# ---------------------------------------------------------------------------
# Runtime Agent Handle
# ---------------------------------------------------------------------------


@dataclass
class RuntimeAgent:
    """Opaque handle returned by a runtime after creating an agent.

    Implementations store framework-specific state internally.
    The public interface exposes only Micro-Agent contracts.
    """

    identity: AgentIdentity
    capabilities: AgentCapabilities
    _internal: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Agent Runtime Interface
# ---------------------------------------------------------------------------


class AgentRuntime(ABC):
    """Abstract runtime interface.

    Implementations bind a Micro-Agent definition to a specific
    agent framework (e.g. Google ADK).

    Rules:
    - No framework-native types cross the public boundary.
    - Only abstractions required by current implementations.
    - Capability reporting for optional features.
    """

    @abstractmethod
    def capabilities(self) -> RuntimeCapabilities:
        """Report runtime capabilities."""

    @abstractmethod
    async def create(self, definition: MicroAgentDefinition) -> RuntimeAgent:
        """Create a runtime agent from a definition."""

    @abstractmethod
    async def start(self, agent: RuntimeAgent) -> None:
        """Start the runtime agent."""

    @abstractmethod
    async def invoke(self, agent: RuntimeAgent, request: AgentRequest) -> AgentResponse:
        """Invoke the runtime agent."""

    async def stream(
        self, agent: RuntimeAgent, request: AgentRequest
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream one invocation when ``capabilities().streaming`` is true."""
        raise NotImplementedError("runtime does not implement streaming")
        yield AgentStreamEvent()  # pragma: no cover

    @abstractmethod
    async def stop(self, agent: RuntimeAgent) -> None:
        """Stop the runtime agent gracefully."""

    @abstractmethod
    async def shutdown(self, agent: RuntimeAgent) -> None:
        """Release all runtime resources."""

    def health_probes(self) -> dict[str, DependencyProbe]:
        """Return active dependency probes exposed by this runtime.

        Runtimes without external dependencies may use the default empty
        mapping. Concrete runtimes can expose provider health checks for the
        executable health endpoint.
        """
        return {}

    async def close(self) -> None:
        """Release runtime-wide resources after agent shutdown."""
        return None
