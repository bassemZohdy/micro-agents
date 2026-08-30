"""Micro-Agent Session.

Session represents current conversational/runtime context.
Session persistence is externally configurable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Session Model
# ---------------------------------------------------------------------------


@dataclass
class SessionContext:
    """Session context containing conversation and caller state."""

    session_id: str = field(default_factory=lambda: str(uuid4()))
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    caller_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionMetadata:
    """Session metadata for management."""

    session_id: str
    created_at: str = ""
    expires_at: str | None = None
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Session Provider Interface
# ---------------------------------------------------------------------------


class SessionProvider(ABC):
    """Abstract session provider interface."""

    @abstractmethod
    async def create(self, session_id: str | None = None) -> SessionContext:
        """Create a new session."""

    @abstractmethod
    async def get(self, session_id: str) -> SessionContext | None:
        """Get a session by ID. Returns None if not found or expired."""

    @abstractmethod
    async def update(self, session: SessionContext) -> None:
        """Update an existing session."""

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """Delete a session."""

    @abstractmethod
    async def list_active(self) -> list[SessionMetadata]:
        """List all active sessions."""


# ---------------------------------------------------------------------------
# In-Memory Session Provider
# ---------------------------------------------------------------------------


class InMemorySessionProvider(SessionProvider):
    """In-memory session provider for development and testing."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}

    async def create(self, session_id: str | None = None) -> SessionContext:
        sid = session_id or str(uuid4())
        session = SessionContext(session_id=sid)
        self._sessions[sid] = session
        return session

    async def get(self, session_id: str) -> SessionContext | None:
        return self._sessions.get(session_id)

    async def update(self, session: SessionContext) -> None:
        self._sessions[session.session_id] = session

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def list_active(self) -> list[SessionMetadata]:
        return [SessionMetadata(session_id=sid, is_active=True) for sid in self._sessions]
