"""SQLite-backed session provider — persistent reference implementation.

Demonstrates the M11 acceptance: multiple runtime replicas share session
state through an external store. Uses only the standard library; expirations
are checked on read, with sliding refresh on update.
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    context TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
)
"""


class SqliteSessionProvider(SessionProvider):
    """Session provider backed by a SQLite database (file shared by replicas)."""

    def __init__(self, path: str = ":memory:", ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = ttl_seconds
        # check_same_thread=False keeps this reference implementation simple;
        # all access is serialized through asyncio.to_thread.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def _expiry(self, ttl_seconds: int | None, now: datetime) -> str | None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        if ttl is None:
            return None
        return _iso(now + timedelta(seconds=ttl))

    def _write(
        self, sid: str, context: SessionContext, now: datetime, expires_at: str | None
    ) -> None:
        payload = {
            "messages": context.messages,
            "metadata": {k: v for k, v in context.metadata.items() if k != "created_at"},
            "caller_context": context.caller_context,
        }
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(session_id, context, created_at, expires_at, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (sid, json.dumps(payload, default=str), _iso(now), expires_at),
        )
        self._conn.commit()

    def _read(self, session_id: str) -> tuple[Any, ...] | None:
        cursor = self._conn.execute(
            "SELECT session_id, context, created_at, expires_at, is_active "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row: tuple[Any, ...] | None = cursor.fetchone()
        return row

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
            session_id=row[0],
            messages=data.get("messages", []),
            metadata={**data.get("metadata", {}), "created_at": row[2]},
            caller_context=data.get("caller_context", {}),
        )

    async def create(
        self, session_id: str | None = None, ttl_seconds: int | None = None
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        context = SessionContext(session_id=sid)
        context.metadata["created_at"] = _iso(now)
        await asyncio.to_thread(self._write, sid, context, now, self._expiry(ttl_seconds, now))
        return context

    async def get(self, session_id: str) -> SessionContext | None:
        row = await asyncio.to_thread(self._read, session_id)
        if row is None:
            return None
        if self._row_expired(row):
            await self.delete(session_id)
            return None
        return self._row_to_context(row)

    async def update(self, session: SessionContext, ttl_seconds: int | None = None) -> None:
        now = _utc_now()
        row = await asyncio.to_thread(self._read, session.session_id)
        created_at = row[2] if row else _iso(now)
        if ttl_seconds is not None or row is None:
            expires_at = self._expiry(ttl_seconds, now)
        else:
            expires_at = row[3]

        def _update() -> None:
            self._write(session.session_id, session, now, expires_at)
            self._conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                (created_at, session.session_id),
            )
            self._conn.commit()

        await asyncio.to_thread(_update)

    async def delete(self, session_id: str) -> None:
        def _delete() -> None:
            self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            self._conn.commit()

        await asyncio.to_thread(_delete)

    async def list_active(self) -> list[SessionMetadata]:
        def _list() -> list[tuple[Any, ...]]:
            cursor = self._conn.execute(
                "SELECT session_id, created_at, expires_at, is_active FROM sessions"
            )
            return cursor.fetchall()

        rows = await asyncio.to_thread(_list)
        active = []
        for sid, created_at, expires_at, is_active in rows:
            if self._row_expired((sid, None, created_at, expires_at, is_active)):
                await self.delete(sid)
                continue
            active.append(
                SessionMetadata(session_id=sid, created_at=created_at, expires_at=expires_at)
            )
        return active

    async def aclose(self) -> None:
        await asyncio.to_thread(self._conn.close)
