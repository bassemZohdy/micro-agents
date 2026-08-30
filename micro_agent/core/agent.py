"""Micro-Agent Core Programming Model.

Core contracts for the Micro-Agent framework. No ADK-native types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Agent Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentIdentity:
    """Explicit agent identity, distinct from user or runtime identity."""

    agent_id: str
    agent_name: str
    agent_version: str
    namespace: str = "default"


# ---------------------------------------------------------------------------
# Agent Capabilities
# ---------------------------------------------------------------------------


@dataclass
class AgentCapabilities:
    """Declared capabilities of a Micro-Agent."""

    streaming: bool = False
    structured_output: bool = False
    memory: bool = False
    mcp: bool = False
    a2a: bool = False


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


@dataclass
class AgentRequest:
    """An invocation request to a Micro-Agent."""

    input: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    caller_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """A response from a Micro-Agent invocation."""

    output: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InvocationOverloadedError(RuntimeError):
    """Raised when an invocation exceeds the configured concurrency limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Invocation concurrency limit reached ({limit})")


# ---------------------------------------------------------------------------
# Agent Context
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Runtime context available during agent invocation."""

    identity: AgentIdentity
    capabilities: AgentCapabilities
    config: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Lifecycle States
# ---------------------------------------------------------------------------


class AgentState(StrEnum):
    """Micro-Agent lifecycle states."""

    CREATED = "created"
    INITIALIZED = "initialized"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Micro-Agent Interface
# ---------------------------------------------------------------------------


class MicroAgent(ABC):
    """Abstract Micro-Agent interface.

    Implementations must not leak runtime-native types through this interface.
    """

    @property
    @abstractmethod
    def identity(self) -> AgentIdentity:
        """Return the agent's identity."""

    @property
    @abstractmethod
    def state(self) -> AgentState:
        """Return the current lifecycle state."""

    @property
    @abstractmethod
    def capabilities(self) -> AgentCapabilities:
        """Return declared capabilities."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the agent. Transition from CREATED to INITIALIZED."""

    @abstractmethod
    async def start(self) -> None:
        """Start the agent. Transition from INITIALIZED to READY."""

    @abstractmethod
    async def invoke(self, request: AgentRequest) -> AgentResponse:
        """Invoke the agent with a request. Must be in READY state."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the agent gracefully. Transition to STOPPED."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release all resources. Called after stop."""
