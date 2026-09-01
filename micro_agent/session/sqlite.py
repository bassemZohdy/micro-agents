"""SQLite-backed session provider — development persistence reference.

The provider demonstrates persistence across provider instances using only the
standard library. It serializes operations per provider and lets SQLite wait
briefly for another connection, but SQLite remains a single-process
development store rather than a production multi-replica backend:
multi-replica deployments must use an external provider (PostgreSQL) instead
of sharing this file across processes.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from micro_agent.session.session import (
    SessionContext,
    SessionMetadata,
    SessionProvider,
    _iso,
    _utc_now,
)
from micro_agent.state import StateConflictError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    context TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1
)
"""


class SqliteSessionProvider(SessionProvider):
    """Session provider backed by SQLite for single-process development use."""

    def __init__(self, path: str = ":memory:", ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = ttl_seconds
        # The connection is used from worker threads, while the async lock
        # below serializes all operations issued through this provider.
        self._conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute(_SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "version" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        self._conn.commit()
        self._lock = asyncio.Lock()
        self._closed = False

    def _expiry(self, ttl_seconds: int | None, now: datetime) -> str | None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        if ttl is None:
            return None
        return _iso(now + timedelta(seconds=ttl))

    def _write(
        self,
        sid: str,
        context: SessionContext,
        created_at: str,
        expires_at: str | None,
    ) -> None:
        payload = {
            "messages": context.messages,
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "version": context.version or 1,
            "metadata": {k: v for k, v in context.metadata.items() if k != "created_at"},
            "caller_context": context.caller_context,
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(session_id, context, created_at, expires_at, is_active, version) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (sid, json.dumps(payload, default=str), created_at, expires_at, context.version or 1),
        )
        self._conn.commit()

    def _read(self, session_id: str) -> tuple[Any, ...] | None:
        cursor = self._conn.execute(
            "SELECT session_id, context, created_at, expires_at, is_active, version "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row: tuple[Any, ...] | None = cursor.fetchone()
        return row

    def _delete(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._conn.commit()

    @staticmethod
    def _row_expired(row: tuple[Any, ...]) -> bool:
        if not row[4]:
            return True
        if not row[3]:
            return False
        try:
            return _utc_now() >= datetime.fromisoformat(row[3])
        except ValueError:
            return False

    @staticmethod
    def _row_to_context(row: tuple[Any, ...]) -> SessionContext:
        data = json.loads(row[1])
        return SessionContext(
            session_id=str(data.get("session_id") or row[0]),
            tenant_id=data.get("tenant_id"),
            version=int(row[5] or data.get("version") or 1),
            messages=data.get("messages", []),
            metadata={**data.get("metadata", {}), "created_at": row[2]},
            caller_context=data.get("caller_context", {}),
        )

    async def create(
        self,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        context = SessionContext(session_id=sid, tenant_id=tenant_id, version=1)
        context.metadata["created_at"] = _iso(now)
        async with self._lock:
            await asyncio.to_thread(
                self._write,
                self._storage_key(sid, tenant_id),
                context,
                _iso(now),
                self._expiry(ttl_seconds, now),
            )
        return context

    @staticmethod
    def _storage_key(session_id: str, tenant_id: str | None = None) -> str:
        return session_id if tenant_id is None else f"{tenant_id}\x1f{session_id}"

    async def get(self, session_id: str, *, tenant_id: str | None = None) -> SessionContext | None:
        async with self._lock:
            row = await asyncio.to_thread(self._read, self._storage_key(session_id, tenant_id))
            if row is None:
                return None
            if self._row_expired(row):
                await asyncio.to_thread(self._delete, self._storage_key(session_id, tenant_id))
                return None
            return self._row_to_context(row)

    async def update(
        self,
        session: SessionContext,
        ttl_seconds: int | None = None,
        *,
        expected_version: int | None = None,
    ) -> None:
        async with self._lock:
            now = _utc_now()
            storage_key = self._storage_key(session.session_id, session.tenant_id)
            row = await asyncio.to_thread(self._read, storage_key)
            actual_version = int(row[5] or 1) if row else 0
            expected = expected_version if expected_version is not None else session.version
            if actual_version == 0:
                if expected not in (0, None):
                    raise StateConflictError("session", session.session_id, expected, 0)
                session.version = 1
            else:
                if expected and expected != actual_version:
                    raise StateConflictError(
                        "session", session.session_id, expected, actual_version
                    )
                session.version = actual_version + 1
            created_at = row[2] if row else _iso(now)
            if ttl_seconds is not None or row is None:
                expires_at = self._expiry(ttl_seconds, now)
            else:
                expires_at = row[3]

            def _update() -> None:
                self._write(storage_key, session, created_at, expires_at)
                self._conn.execute(
                    "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                    (created_at, storage_key),
                )
                self._conn.commit()

            await asyncio.to_thread(_update)

    async def delete(self, session_id: str, *, tenant_id: str | None = None) -> None:
        async with self._lock:
            await asyncio.to_thread(self._delete, self._storage_key(session_id, tenant_id))

    def _list_active(self, tenant_id: str | None = None) -> list[SessionMetadata]:
        cursor = self._conn.execute(
            "SELECT session_id, context, created_at, expires_at, is_active, version FROM sessions"
        )
        rows = cursor.fetchall()
        active = []
        expired: list[str] = []
        for row in rows:
            sid, _context, created_at, expires_at, is_active, _version = row
            if self._row_expired(row):
                expired.append(sid)
                continue
            context = self._row_to_context(row)
            if tenant_id is not None and context.tenant_id != tenant_id:
                continue
            active.append(
                SessionMetadata(
                    session_id=context.session_id,
                    tenant_id=context.tenant_id,
                    version=context.version,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            )
        for sid in expired:
            self._delete(sid)
        return active

    async def list_active(self, *, tenant_id: str | None = None) -> list[SessionMetadata]:
        async with self._lock:
            return await asyncio.to_thread(self._list_active, tenant_id)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return

            def _close() -> None:
                self._conn.close()

            await asyncio.to_thread(_close)
            self._closed = True
