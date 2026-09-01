"""PostgreSQL memory provider and operation registry for multi-process state.

- :class:`PostgresMemoryProvider` implements the Micro-Agent memory SPI with
  tenant- and scope-partitioned entries, optimistic version-based conflict
  detection (stale writers raise ``StateConflictError``), policy TTL/capacity
  enforcement, and keyword search over entry text.
- :class:`PostgresIdempotencyStore` implements the runtime's operation
  registry contract on PostgreSQL: ``claim`` atomically reserves idempotency
  keys across processes, so two workers racing the same key cannot both
  execute the operation, and completed results are deduplicated until their
  TTL expires.

Both close their pools on shutdown and remain usable as readiness probes.
DSNs are never included in logs, errors, or ``repr``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from micro_agent.memory.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.security.side_effects import Operation, OperationResult
from micro_agent.state import StateConflictError


def _tenant_key(tenant_id: str | None) -> str:
    # PostgreSQL columns are NOT NULL here, so the unscoped namespace uses the
    # empty string, mirroring the SQLite/Redis storage-key convention.
    return tenant_id or ""


def _storage_key(idempotency_key: str, tenant_id: str | None = None) -> str:
    """Keep legacy unscoped keys stable while isolating tenant keys."""
    return idempotency_key if tenant_id is None else f"{tenant_id}\x1f{idempotency_key}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _json_loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgresMemoryProvider(MemoryProvider):
    """Memory entries in PostgreSQL, shared across processes."""

    def __init__(
        self,
        dsn: str,
        *,
        policy: MemoryPolicy | None = None,
        table: str = "micro_agent_memory",
    ) -> None:
        try:
            import asyncpg  # type: ignore[import-untyped]  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise RuntimeError(
                "the PostgreSQL memory provider requires the optional "
                "'postgres' extra ('micro-agents[postgres]')"
            ) from exc
        self.dsn = dsn
        self.policy = policy or MemoryPolicy()
        self._table = table
        self._pool: Any = None
        self._connect_lock = asyncio.Lock()
        self._schema_ready = False

    def __repr__(self) -> str:
        return "PostgresMemoryProvider(dsn=***)"

    async def _pool_ready(self) -> Any:
        if self._pool is None:
            async with self._connect_lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        if not self._schema_ready:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", f"ddl:{self._table}"
                )
                await conn.execute(
                    f"""
                        CREATE TABLE IF NOT EXISTS {self._table} (
                            tenant_id TEXT NOT NULL DEFAULT '',
                            scope TEXT NOT NULL DEFAULT 'agent',
                            key TEXT NOT NULL,
                            value JSONB NOT NULL,
                            version INTEGER NOT NULL DEFAULT 1,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            expires_at TIMESTAMPTZ,
                            PRIMARY KEY (tenant_id, scope, key)
                        )
                        """
                )
            self._schema_ready = True
        return self._pool

    async def store(self, entry: MemoryEntry, *, expected_version: int | None = None) -> None:
        pool = await self._pool_ready()
        tenant = _tenant_key(entry.tenant_id)
        scope = entry.scope or "agent"
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self.policy.ttl_seconds)
            if self.policy.ttl_seconds is not None
            else None
        )
        row = await pool.fetchrow(
            f"SELECT version FROM {self._table} WHERE tenant_id = $1 AND scope = $2 AND key = $3",
            tenant,
            scope,
            entry.key,
        )
        actual = int(row["version"]) if row else 0
        if actual == 0:
            expected = expected_version if expected_version is not None else entry.version
            if expected not in (0, None):
                raise StateConflictError("memory", entry.key, expected, 0)
            new_version = 1
            await pool.execute(
                f"""
                INSERT INTO {self._table} (tenant_id, scope, key, value, version, expires_at)
                VALUES ($1, $2, $3, $4, 1, $5)
                ON CONFLICT (tenant_id, scope, key) DO UPDATE
                SET value = EXCLUDED.value,
                    version = {self._table}.version + 1,
                    expires_at = EXCLUDED.expires_at,
                    created_at = now()
                """,
                tenant,
                scope,
                entry.key,
                _json_dumps({"value": entry.value, "metadata": entry.metadata}),
                expires_at,
            )
        else:
            expected = expected_version if expected_version is not None else entry.version
            if expected and expected != actual:
                raise StateConflictError("memory", entry.key, expected, actual)
            new_version = actual + 1
            result = await pool.fetchval(
                f"""
                UPDATE {self._table}
                SET value = $4, version = version + 1, expires_at = $5, created_at = now()
                WHERE tenant_id = $1 AND scope = $2 AND key = $3 AND version = $6
                RETURNING version
                """,
                tenant,
                scope,
                entry.key,
                _json_dumps({"value": entry.value, "metadata": entry.metadata}),
                expires_at,
                actual,
            )
            if result is None:
                # Another writer bumped the version between the read and the
                # guarded write; the caller must re-read and retry.
                raise StateConflictError("memory", entry.key, expected, actual)
        entry.version = new_version
        if self.policy.max_entries is not None:
            await pool.execute(
                f"""
                DELETE FROM {self._table}
                WHERE tenant_id = $1 AND scope = $2 AND (scope, key) IN (
                    SELECT scope, key FROM {self._table}
                    WHERE tenant_id = $1 AND scope = $2
                    ORDER BY created_at DESC
                    OFFSET $3
                )
                """,
                tenant,
                scope,
                self.policy.max_entries,
            )

    async def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[MemoryEntry]:
        entries = await self.list_entries(scope, tenant_id=tenant_id)
        terms = query.lower().split()
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in entries:
            text = str(entry.value).lower()
            matches = sum(1 for term in terms if term in text)
            if terms and matches == 0:
                continue
            relevance = matches / len(terms) if terms else 1.0
            scored.append((relevance, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    async def get(
        self, key: str, scope: str | None = None, *, tenant_id: str | None = None
    ) -> MemoryEntry | None:
        pool = await self._pool_ready()
        await self._purge_expired(pool)
        row = await pool.fetchrow(
            f"""
            SELECT tenant_id, scope, key, value, version FROM {self._table}
            WHERE tenant_id = $1 AND scope = $2 AND key = $3
            """,
            _tenant_key(tenant_id),
            scope or "agent",
            key,
        )
        if row is None:
            return None
        return self._row_to_entry(row)

    async def delete(
        self, key: str, scope: str | None = None, *, tenant_id: str | None = None
    ) -> bool:
        pool = await self._pool_ready()
        result: str = await pool.execute(
            f"DELETE FROM {self._table} WHERE tenant_id = $1 AND scope = $2 AND key = $3",
            _tenant_key(tenant_id),
            scope or "agent",
            key,
        )
        return result == "DELETE 1"

    async def list_entries(
        self, scope: str | None = None, *, tenant_id: str | None = None
    ) -> list[MemoryEntry]:
        pool = await self._pool_ready()
        await self._purge_expired(pool)
        if scope is None and tenant_id is None:
            rows = await pool.fetch(
                f"""
                SELECT tenant_id, scope, key, value, version FROM {self._table}
                ORDER BY created_at DESC
                """
            )
        elif scope is None:
            rows = await pool.fetch(
                f"""
                SELECT tenant_id, scope, key, value, version FROM {self._table}
                WHERE tenant_id = $1 ORDER BY created_at DESC
                """,
                _tenant_key(tenant_id),
            )
        elif tenant_id is None:
            rows = await pool.fetch(
                f"""
                SELECT tenant_id, scope, key, value, version FROM {self._table}
                WHERE scope = $1 ORDER BY created_at DESC
                """,
                scope,
            )
        else:
            rows = await pool.fetch(
                f"""
                SELECT tenant_id, scope, key, value, version FROM {self._table}
                WHERE tenant_id = $1 AND scope = $2 ORDER BY created_at DESC
                """,
                _tenant_key(tenant_id),
                scope,
            )
        return [self._row_to_entry(row) for row in rows]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False

    async def _purge_expired(self, pool: Any) -> None:
        await pool.execute(
            f"DELETE FROM {self._table} WHERE expires_at IS NOT NULL AND expires_at <= now()"
        )

    @staticmethod
    def _row_to_entry(row: Any) -> MemoryEntry:
        payload = _json_loads(row["value"]) or {}
        return MemoryEntry(
            key=row["key"],
            value=payload.get("value"),
            scope=row["scope"],
            metadata=payload.get("metadata") or {},
            tenant_id=row["tenant_id"] or None,
            version=int(row["version"]),
        )


class PostgresIdempotencyStore:
    """Atomic, cross-process operation registry on PostgreSQL.

    Mirrors the Redis registry's contract: ``claim`` reserves the key with a
    single atomic insert (or reclaims an expired reservation), only the
    owning operation may publish its result, and completed results stay
    deduplicated until the TTL expires.
    """

    def __init__(
        self,
        dsn: str,
        *,
        ttl_seconds: int = 86400,
        table: str = "micro_agent_idempotency",
    ) -> None:
        try:
            import asyncpg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised without extra
            raise RuntimeError(
                "the PostgreSQL operation registry requires the optional "
                "'postgres' extra ('micro-agents[postgres]')"
            ) from exc
        self.dsn = dsn
        self._ttl = ttl_seconds
        self._table = table
        self._pool: Any = None
        self._connect_lock = asyncio.Lock()
        self._schema_ready = False

    def __repr__(self) -> str:
        return "PostgresIdempotencyStore(dsn=***)"

    async def _pool_ready(self) -> Any:
        if self._pool is None:
            async with self._connect_lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
        if not self._schema_ready:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))", f"ddl:{self._table}"
                )
                await conn.execute(
                    f"""
                        CREATE TABLE IF NOT EXISTS {self._table} (
                            key TEXT PRIMARY KEY,
                            operation_id TEXT NOT NULL DEFAULT '',
                            status TEXT NOT NULL,
                            output JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            expires_at TIMESTAMPTZ NOT NULL
                        )
                        """
                )
            self._schema_ready = True
        return self._pool

    async def claim(self, operation: Operation) -> tuple[bool, OperationResult | None]:
        """Atomically reserve the operation's idempotency key.

        Returns ``(True, None)`` when this call reserved the key, ``(False,
        prior)`` when the key already carries a completed result, and
        ``(False, in_progress)`` when another worker still holds an unexpired
        reservation.
        """
        key = operation.idempotency_key
        if not key:
            return True, None
        pool = await self._pool_ready()
        storage_key = _storage_key(key, operation.tenant_id)
        expires_at = datetime.now(UTC) + timedelta(seconds=self._ttl)
        reserved = await pool.fetchval(
            f"""
            INSERT INTO {self._table} (key, operation_id, status, expires_at)
            VALUES ($1, $2, 'in_progress', $3)
            ON CONFLICT (key) DO NOTHING
            RETURNING key
            """,
            storage_key,
            operation.operation_id,
            expires_at,
        )
        if reserved is not None:
            return True, None
        # The key exists: reclaim it when the prior reservation expired,
        # otherwise report the prior/in-progress result.
        reclaimed = await pool.fetchval(
            f"""
            UPDATE {self._table}
            SET operation_id = $2, status = 'in_progress', output = NULL,
                error = NULL, created_at = now(), expires_at = $3
            WHERE key = $1 AND expires_at <= now()
            RETURNING key
            """,
            storage_key,
            operation.operation_id,
            expires_at,
        )
        if reclaimed is not None:
            return True, None
        prior = await self._get_result(storage_key)
        if prior is None:
            prior = OperationResult(status="in_progress")
        return False, replace(prior, was_deduplicated=True)

    async def is_duplicate(self, operation: Operation) -> bool:
        """Return whether the key is currently reserved or completed."""
        if not operation.idempotency_key:
            return False
        pool = await self._pool_ready()
        row = await pool.fetchval(
            f"""
            SELECT key FROM {self._table}
            WHERE key = $1 AND expires_at > now()
            """,
            _storage_key(operation.idempotency_key, operation.tenant_id),
        )
        return row is not None

    async def find_by_idempotency_key(
        self, key: str, tenant_id: str | None = None
    ) -> OperationResult | None:
        """Return a reservation or completed result for ``key``."""
        return await self._get_result(_storage_key(key, tenant_id))

    async def record(self, operation: Operation, result: OperationResult) -> None:
        """Complete a reservation while retaining the idempotency TTL.

        Only the operation that owns the reservation may publish a result. A
        result arriving after the reservation expired or was reclaimed is
        ignored, preventing a late worker from overwriting a newer attempt.
        """
        key = operation.idempotency_key
        if not key:
            return
        pool = await self._pool_ready()
        await pool.execute(
            f"""
            UPDATE {self._table}
            SET status = $3, output = $4, error = $5, expires_at = $6
            WHERE key = $1 AND operation_id = $2
            """,
            _storage_key(key, operation.tenant_id),
            operation.operation_id,
            result.status,
            _json_dumps(result.output) if result.output is not None else None,
            result.error,
            datetime.now(UTC) + timedelta(seconds=self._ttl),
        )

    async def health_check(self) -> bool:
        """Return whether PostgreSQL answers a trivial query."""
        pool = await self._pool_ready()
        await pool.fetchval("SELECT 1")
        return True

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_ready = False

    async def _get_result(self, storage_key: str) -> OperationResult | None:
        pool = await self._pool_ready()
        row = await pool.fetchrow(
            f"""
            SELECT operation_id, status, output, error FROM {self._table}
            WHERE key = $1 AND expires_at > now()
            """,
            storage_key,
        )
        if row is None:
            return None
        output = _json_loads(row["output"]) if row["output"] is not None else None
        return OperationResult(
            operation_id=row["operation_id"],
            status=row["status"],
            output=output,
            error=row["error"],
        )


__all__ = [
    "PostgresIdempotencyStore",
    "PostgresMemoryProvider",
]
