"""Micro-Agent Core Programming Model.

Core contracts for the Micro-Agent framework. No ADK-native types.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    # Runtime-safe: annotations are lazy, and security.identity imports
    # AgentIdentity from this module, so a runtime import would cycle.
    from micro_agent.security.identity import CallerIdentity, UserContext

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
    """An invocation request to a Micro-Agent.

    ``caller_identity``/``user_context`` are set only from a configured
    transport Authenticator — never from caller-supplied request metadata.
    """

    input: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    caller_metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None
    caller_identity: CallerIdentity | None = None
    user_context: UserContext | None = None
    continuation_id: str | None = None
    approval_decision: str | None = None
    checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        """Reject invalid caller-provided deadlines before runtime work starts."""
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.approval_decision is not None and self.approval_decision not in (
            "approve",
            "deny",
        ):
            raise ValueError("approval_decision must be 'approve' or 'deny'")
        if self.continuation_id is not None and self.approval_decision is None:
            raise ValueError("continuation_id requires approval_decision")
        if self.checkpoint_id is not None and not self.checkpoint_id.strip():
            raise ValueError("checkpoint_id must not be empty")
        if self.checkpoint_id is not None and self.continuation_id is not None:
            raise ValueError("checkpoint_id cannot be combined with continuation_id")


@dataclass
class AgentResponse:
    """A response from a Micro-Agent invocation."""

    output: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    session_id: str | None = None
    status: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentStreamEvent:
    """One runtime-neutral streaming event."""

    delta: str = ""
    response: AgentResponse | None = None


class InvocationOverloadedError(RuntimeError):
    """Raised when an invocation exceeds the configured concurrency limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Invocation concurrency limit reached ({limit})")


class AuthenticationError(RuntimeError):
    """Raised when a caller cannot be authenticated by the transport layer."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated caller is not allowed to invoke an agent."""


class DependencyUnavailableError(ConnectionError):
    """Raised when a required model, tool, or state dependency is unavailable."""


class ContinuationNotFoundError(RuntimeError):
    """Raised when an approval continuation is unknown, expired, or foreign."""


class CheckpointNotFoundError(RuntimeError):
    """Raised when a checkpoint is unknown, expired, foreign, or unavailable."""


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

    async def stream(self, request: AgentRequest) -> AsyncIterator[AgentStreamEvent]:
        """Stream an invocation when the selected runtime supports it."""
        raise NotImplementedError("agent runtime does not implement streaming")
        yield AgentStreamEvent()  # pragma: no cover

    @abstractmethod
    async def stop(self) -> None:
        """Stop the agent gracefully. Transition to STOPPED."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release all resources. Called after stop."""
