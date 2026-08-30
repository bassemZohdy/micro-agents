"""Micro-Agent Runtime SPI (Service Provider Interface).

Defines the smallest useful runtime abstraction.
No framework-native types cross the public boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from micro_agent.core import (
    AgentCapabilities,
    AgentIdentity,
    AgentRequest,
    AgentResponse,
)
from micro_agent.definition import MicroAgentDefinition

_RUNTIME_CAPABILITY_NAMES = frozenset(
    {"streaming", "memory", "mcp", "a2a", "structured_output", "checkpointing"}
)

# ---------------------------------------------------------------------------
# Runtime Capabilities
# ---------------------------------------------------------------------------


@dataclass
class RuntimeCapabilities:
    """Capabilities reported by a runtime implementation."""

    streaming: bool = False
    memory: bool = False
    mcp: bool = False
    a2a: bool = False
    structured_output: bool = False
    checkpointing: bool = False

    def supports(self, capability: str) -> bool:
        """Return whether this runtime advertises a named capability."""
        return capability in _RUNTIME_CAPABILITY_NAMES and bool(getattr(self, capability, False))

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

    @abstractmethod
    async def stop(self, agent: RuntimeAgent) -> None:
        """Stop the runtime agent gracefully."""

    @abstractmethod
    async def shutdown(self, agent: RuntimeAgent) -> None:
        """Release all runtime resources."""
