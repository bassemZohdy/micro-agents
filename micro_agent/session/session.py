"""Micro-Agent Session.

Session represents current conversational/runtime context.
Session persistence is externally configurable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from micro_agent.state import StateConflictError


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
    tenant_id: str | None = None
    version: int = 0


@dataclass
class SessionMetadata:
    """Session metadata for management."""

    session_id: str
    created_at: str = ""
    expires_at: str | None = None
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    tenant_id: str | None = None
    version: int = 0


# ---------------------------------------------------------------------------
# Session Provider Interface
# ---------------------------------------------------------------------------


class SessionProvider(ABC):
    """Abstract session provider interface."""

    @abstractmethod
    async def create(
        self,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> SessionContext:
        """Create a new session. Optional ttl_seconds overrides the provider default."""

    @abstractmethod
    async def get(self, session_id: str, *, tenant_id: str | None = None) -> SessionContext | None:
        """Get a session by ID. Returns None if not found or expired."""

    @abstractmethod
    async def update(
        self,
        session: SessionContext,
        ttl_seconds: int | None = None,
        *,
        expected_version: int | None = None,
    ) -> None:
        """Update an existing session. Optional ttl_seconds refreshes expiration."""

    @abstractmethod
    async def delete(self, session_id: str, *, tenant_id: str | None = None) -> None:
        """Delete a session."""

    @abstractmethod
    async def list_active(self, *, tenant_id: str | None = None) -> list[SessionMetadata]:
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

    @staticmethod
    def _key(session_id: str, tenant_id: str | None = None) -> str:
        return session_id if tenant_id is None else f"{tenant_id}\x1f{session_id}"

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
        self,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        key = self._key(sid, tenant_id)
        session = SessionContext(session_id=sid, tenant_id=tenant_id, version=1)
        session.metadata["created_at"] = _iso(now)
        self._sessions[key] = session
        self._metadata[key] = SessionMetadata(
            session_id=sid,
            tenant_id=tenant_id,
            version=1,
            created_at=_iso(now),
            expires_at=self._expiry(ttl_seconds, now),
            is_active=True,
        )
        return session

    async def get(self, session_id: str, *, tenant_id: str | None = None) -> SessionContext | None:
        key = self._key(session_id, tenant_id)
        meta = self._metadata.get(key)
        if meta is not None and self._is_expired(meta):
            await self.delete(session_id, tenant_id=tenant_id)
            return None
        context = self._sessions.get(key)
        return deepcopy(context) if context is not None else None

    async def update(
        self,
        session: SessionContext,
        ttl_seconds: int | None = None,
        *,
        expected_version: int | None = None,
    ) -> None:
        tenant_id = session.tenant_id
        key = self._key(session.session_id, tenant_id)
        meta = self._metadata.get(key)
        if meta is None:
            now = _utc_now()
            if expected_version not in (None, 0) or session.version not in (0, 1):
                expected = expected_version if expected_version is not None else session.version
                raise StateConflictError("session", session.session_id, expected, 0)
            session.version = 1
            meta = SessionMetadata(
                session_id=session.session_id,
                tenant_id=tenant_id,
                version=1,
                created_at=_iso(now),
                expires_at=self._expiry(ttl_seconds, now),
                is_active=True,
            )
            self._metadata[key] = meta
        elif ttl_seconds is not None:
            expected = expected_version if expected_version is not None else session.version
            if expected and expected != meta.version:
                raise StateConflictError("session", session.session_id, expected, meta.version)
            session.version = meta.version + 1
            meta.version = session.version
            # Sliding expiration: refresh from the update time.
            meta.expires_at = self._expiry(ttl_seconds, _utc_now())
        else:
            expected = expected_version if expected_version is not None else session.version
            if expected and expected != meta.version:
                raise StateConflictError("session", session.session_id, expected, meta.version)
            session.version = meta.version + 1
            meta.version = session.version
        self._sessions[key] = session

    async def delete(self, session_id: str, *, tenant_id: str | None = None) -> None:
        key = self._key(session_id, tenant_id)
        self._sessions.pop(key, None)
        self._metadata.pop(key, None)

    async def list_active(self, *, tenant_id: str | None = None) -> list[SessionMetadata]:
        now = _utc_now()
        active: list[SessionMetadata] = []
        expired: list[str] = []
        for key, meta in self._metadata.items():
            if tenant_id is not None and meta.tenant_id != tenant_id:
                continue
            if self._is_expired(meta, now):
                expired.append(key)
                continue
            active.append(meta)
        for key in expired:
            expired_meta = self._metadata.get(key)
            if expired_meta is not None:
                await self.delete(expired_meta.session_id, tenant_id=expired_meta.tenant_id)
        return active
