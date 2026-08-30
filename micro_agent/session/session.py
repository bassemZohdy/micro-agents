"""Micro-Agent Session.

Session represents current conversational/runtime context.
Session persistence is externally configurable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


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
    async def create(
        self, session_id: str | None = None, ttl_seconds: int | None = None
    ) -> SessionContext:
        """Create a new session. Optional ttl_seconds overrides the provider default."""

    @abstractmethod
    async def get(self, session_id: str) -> SessionContext | None:
        """Get a session by ID. Returns None if not found or expired."""

    @abstractmethod
    async def update(self, session: SessionContext, ttl_seconds: int | None = None) -> None:
        """Update an existing session. Optional ttl_seconds refreshes expiration."""

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
    """In-memory session provider for development and testing.

    Tracks creation/expiration timestamps. A TTL may be given per provider
    (default for all sessions) or per create() call.
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._metadata: dict[str, SessionMetadata] = {}
        self._ttl_seconds = ttl_seconds

    def _expiry(self, ttl_seconds: int | None, now: datetime) -> str | None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        if ttl is None:
            return None
        return _iso(now + timedelta(seconds=ttl))

    def _is_expired(self, meta: SessionMetadata, now: datetime | None = None) -> bool:
        if not meta.is_active or not meta.expires_at:
            return not meta.is_active
        reference = now or _utc_now()
        try:
            expires = datetime.fromisoformat(meta.expires_at)
        except ValueError:
            return False
        return reference >= expires

    async def create(
        self, session_id: str | None = None, ttl_seconds: int | None = None
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        session = SessionContext(session_id=sid)
        session.metadata["created_at"] = _iso(now)
        self._sessions[sid] = session
        self._metadata[sid] = SessionMetadata(
            session_id=sid,
            created_at=_iso(now),
            expires_at=self._expiry(ttl_seconds, now),
            is_active=True,
        )
        return session

    async def get(self, session_id: str) -> SessionContext | None:
        meta = self._metadata.get(session_id)
        if meta is not None and self._is_expired(meta):
            await self.delete(session_id)
            return None
        return self._sessions.get(session_id)

    async def update(self, session: SessionContext, ttl_seconds: int | None = None) -> None:
        self._sessions[session.session_id] = session
        meta = self._metadata.get(session.session_id)
        if meta is None:
            now = _utc_now()
            meta = SessionMetadata(
                session_id=session.session_id,
                created_at=_iso(now),
                expires_at=self._expiry(ttl_seconds, now),
                is_active=True,
            )
            self._metadata[session.session_id] = meta
        elif ttl_seconds is not None:
            # Sliding expiration: refresh from the update time.
            meta.expires_at = self._expiry(ttl_seconds, _utc_now())

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._metadata.pop(session_id, None)

    async def list_active(self) -> list[SessionMetadata]:
        now = _utc_now()
        active: list[SessionMetadata] = []
        expired: list[str] = []
        for sid, meta in self._metadata.items():
            if self._is_expired(meta, now):
                expired.append(sid)
                continue
            active.append(meta)
        for sid in expired:
            await self.delete(sid)
        return active
