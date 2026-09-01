"""PostgreSQL session provider: concurrency-safe external session state.

Implements the :class:`~micro_agent.session.session.SessionProvider` SPI on
PostgreSQL so independent service processes share session state. Updates use
optimistic concurrency: a stale writer whose expected version no longer
matches the stored row fails with :class:`~micro_agent.state.StateConflictError`
instead of silently losing its update. Expired rows are deleted lazily on
access and purged by ``list_active``. The provider closes its connection pool
on shutdown and ``list_active`` doubles as the startup readiness probe.

The DSN is never included in logs, errors, or ``repr``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from micro_agent.session.session import (
    SessionContext,
    SessionMetadata,
    SessionProvider,
)
from micro_agent.state import StateConflictError


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _tenant_key(tenant_id: str | None) -> str:
    # PostgreSQL primary keys cannot contain NULL, so the unscoped namespace
    # uses the empty string, mirroring the SQLite storage-key convention.
    return tenant_id or ""


class PostgresSessionProvider(SessionProvider):
    """Session state in PostgreSQL, safe for multi-process deployments."""

    def __init__(
        self,
        dsn: str,
        *,
        ttl_seconds: int | None = None,
        table: str = "micro_agent_sessions",
    ) -> None:
        try:
            import asyncpg  # type: ignore[import-untyped]  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise RuntimeError(
                "the PostgreSQL session provider requires the optional "
                "'postgres' extra ('micro-agents[postgres]')"
            ) from exc
        self._dsn = dsn
        self._default_ttl = ttl_seconds
        self._table = table
        self._pool: Any = None
        self._connect_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "PostgresSessionProvider(dsn=***)"

    async def _ensure_pool(self) -> Any:
        if self._pool is None:
            async with self._connect_lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def _ensure_schema(self, pool: Any) -> None:
        # Concurrent replicas bootstrap together in multi-process
        # deployments; the advisory lock serializes DDL so the
        # IF-NOT-EXISTS create cannot race itself.
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ddl:{self._table}")
            await conn.execute(
                f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        tenant_id TEXT NOT NULL DEFAULT '',
                        session_id TEXT NOT NULL,
                        messages JSONB NOT NULL DEFAULT '[]',
                        metadata JSONB NOT NULL DEFAULT '{{}}',
                        caller_context JSONB NOT NULL DEFAULT '{{}}',
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        expires_at TIMESTAMPTZ,
                        PRIMARY KEY (tenant_id, session_id)
                    )
                    """
            )

    async def _pool_ready(self) -> Any:
        pool = await self._ensure_pool()
        if not getattr(self, "_schema_ready", False):
            await self._ensure_schema(pool)
            self._schema_ready = True
        return pool

    async def create(
        self,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> SessionContext:
        pool = await self._pool_ready()
        session = SessionContext(
            session_id=session_id or SessionContext().session_id, tenant_id=tenant_id
        )
        row = await pool.fetchrow(
            f"""
            INSERT INTO {self._table} (tenant_id, session_id, version, expires_at)
            VALUES ($1, $2, 1, $3)
            ON CONFLICT (tenant_id, session_id)
                DO UPDATE SET expires_at = COALESCE($3, {self._table}.expires_at)
            RETURNING session_id, version
            """,
            _tenant_key(tenant_id),
            session.session_id,
            self._expires_at(ttl_seconds),
        )
        session.version = int(row["version"])
        return session

    async def get(self, session_id: str, *, tenant_id: str | None = None) -> SessionContext | None:
        pool = await self._pool_ready()
        key = _tenant_key(tenant_id)
        row = await pool.fetchrow(
            f"""
            DELETE FROM {self._table}
            WHERE tenant_id = $1 AND session_id = $2
              AND expires_at IS NOT NULL AND expires_at <= now()
            RETURNING session_id
            """,
            key,
            session_id,
        )
        if row is not None:
            return None
        row = await pool.fetchrow(
            f"""
            SELECT session_id, tenant_id, messages, metadata, caller_context, version, expires_at
            FROM {self._table} WHERE tenant_id = $1 AND session_id = $2
            """,
            key,
            session_id,
        )
        if row is None:
            return None
        return SessionContext(
            session_id=row["session_id"],
            tenant_id=row["tenant_id"] or None,
            messages=json.loads(row["messages"]),
            metadata=json.loads(row["metadata"]),
            caller_context=json.loads(row["caller_context"]),
            version=int(row["version"]),
        )

    async def update(
        self,
        session: SessionContext,
        ttl_seconds: int | None = None,
        *,
        expected_version: int | None = None,
    ) -> None:
        pool = await self._pool_ready()
        key = _tenant_key(session.tenant_id)
        expected = expected_version if expected_version is not None else session.version
        row = await pool.fetchrow(
            f"SELECT version FROM {self._table} WHERE tenant_id = $1 AND session_id = $2",
            key,
            session.session_id,
        )
        actual = int(row["version"]) if row else 0
        if actual == 0:
            if expected not in (0, None):
                raise StateConflictError("session", session.session_id, expected, 0)
            session.version = 1
            await pool.execute(
                f"""
                INSERT INTO {self._table}
                    (tenant_id, session_id, messages, metadata, caller_context, version, expires_at)
                VALUES ($1, $2, $3, $4, $5, 1, $6)
                ON CONFLICT (tenant_id, session_id) DO UPDATE SET
                    messages = EXCLUDED.messages,
                    metadata = EXCLUDED.metadata,
                    caller_context = EXCLUDED.caller_context,
                    version = {self._table}.version + 1,
                    expires_at = COALESCE(EXCLUDED.expires_at, {self._table}.expires_at)
                """,
                key,
                session.session_id,
                json.dumps(session.messages, default=str),
                json.dumps(session.metadata, default=str),
                json.dumps(session.caller_context, default=str),
                self._expires_at(ttl_seconds),
            )
            return
        if expected and expected != actual:
            raise StateConflictError("session", session.session_id, expected, actual)
        row = await pool.fetchrow(
            f"""
            UPDATE {self._table}
            SET messages = $3, metadata = $4, caller_context = $5,
                version = version + 1, expires_at = COALESCE($6, expires_at)
            WHERE tenant_id = $1 AND session_id = $2 AND version = $7
            RETURNING version
            """,
            key,
            session.session_id,
            json.dumps(session.messages, default=str),
            json.dumps(session.metadata, default=str),
            json.dumps(session.caller_context, default=str),
            self._expires_at(ttl_seconds),
            actual,
        )
        if row is None:
            # Another writer bumped the version between the read and the
            # guarded write; the caller must re-read and retry.
            raise StateConflictError("session", session.session_id, expected, actual)
        session.version = int(row["version"])

    async def delete(self, session_id: str, *, tenant_id: str | None = None) -> None:
        pool = await self._pool_ready()
        await pool.execute(
            f"DELETE FROM {self._table} WHERE tenant_id = $1 AND session_id = $2",
            _tenant_key(tenant_id),
            session_id,
        )

    async def list_active(self, *, tenant_id: str | None = None) -> list[SessionMetadata]:
        pool = await self._pool_ready()
        await pool.execute(
            f"DELETE FROM {self._table} WHERE expires_at IS NOT NULL AND expires_at <= now()"
        )
        if tenant_id is None:
            rows = await pool.fetch(
                f"SELECT session_id, tenant_id, version, created_at, expires_at FROM {self._table}"
            )
        else:
            rows = await pool.fetch(
                f"""
                SELECT session_id, tenant_id, version, created_at, expires_at
                FROM {self._table} WHERE tenant_id = $1
                """,
                _tenant_key(tenant_id),
            )
        return [
            SessionMetadata(
                session_id=row["session_id"],
                tenant_id=row["tenant_id"] or None,
                version=int(row["version"]),
                created_at=row["created_at"].isoformat(),
                expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
                is_active=True,
            )
            for row in rows
        ]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False

    def _expires_at(self, ttl_seconds: int | None) -> datetime | None:
        effective = ttl_seconds if ttl_seconds is not None else self._default_ttl
        if effective is None:
            return None
        return _utc_now() + timedelta(seconds=effective)


__all__ = ["PostgresSessionProvider"]
