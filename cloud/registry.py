"""Lease-based agent registry for Micro-Agent Cloud (C1/C5).

Stores versioned :class:`~cloud.descriptors.AgentDescriptor` entries with
lease-based health: a registration is healthy until its TTL lapses unless
heartbeats renew it. Expired entries stay queryable with their staleness —
per the C0 failure model the registry serves stale descriptors with a stated
age instead of hiding agents that stopped heartbeating.

The registry keeps only control-plane state: semantic descriptors and health
rollups. It is never on an agent's serving path. The HTTP app is a plain
FastAPI surface; deploy it with any ASGI server (``python -m cloud.registry``
runs uvicorn). Authentication for the registry API itself is C2+ work and is
deliberately out of scope here. In-memory and SQLite stores share the same
async API; SQLite persists lease state across process restarts.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException

from cloud.descriptors import AgentDescriptor, DescriptorError

DEFAULT_LEASE_SECONDS = 300.0
# Expired entries remain queryable (stale) for this long before removal.
_STALE_RETENTION_SECONDS = 86_400.0


def _monotonic_now() -> float:
    return time.monotonic()


def _wall_clock_now() -> float:
    return time.time()


@dataclass
class RegistryEntry:
    """One registration: the descriptor plus its technical health rollup."""

    descriptor: AgentDescriptor
    registered_at: float
    lease_expires_at: float
    _clock: Callable[[], float] = field(default=_monotonic_now, repr=False, compare=False)

    @property
    def healthy(self) -> bool:
        return bool(self._clock() < self.lease_expires_at)

    def age_seconds(self) -> float:
        return float(max(0.0, self._clock() - self.registered_at))

    def expires_in_seconds(self) -> float:
        return float(max(0.0, self.lease_expires_at - self._clock()))


class UnknownAgentError(KeyError):
    """Raised for heartbeat/deregister of an unregistered agent version."""


class InMemoryAgentRegistry:
    """Lease-based descriptor registry, safe for single-process deployment."""

    def __init__(self, *, default_lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        self._default_lease = default_lease_seconds
        self._entries: dict[tuple[str, str], RegistryEntry] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, descriptor: AgentDescriptor, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        if not descriptor.name or not descriptor.version:
            raise DescriptorError("descriptor must carry a name and version")
        if descriptor.schema_version != AgentDescriptor().schema_version:
            raise DescriptorError(
                f"unsupported descriptor schema version '{descriptor.schema_version}'"
            )
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        if ttl <= 0:
            raise DescriptorError("registration ttl must be positive")
        now = time.monotonic()
        async with self._lock:
            entry = RegistryEntry(
                descriptor=descriptor,
                registered_at=now,
                lease_expires_at=now + ttl,
            )
            self._entries[(descriptor.name, descriptor.version)] = entry
        return entry

    async def heartbeat(
        self, name: str, version: str, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        if ttl <= 0:
            raise DescriptorError("heartbeat ttl must be positive")
        async with self._lock:
            entry = self._entries.get((name, version))
            if entry is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")
            entry.lease_expires_at = time.monotonic() + ttl
            return entry

    async def deregister(self, name: str, version: str) -> None:
        async with self._lock:
            if self._entries.pop((name, version), None) is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")

    async def query(
        self,
        *,
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> list[RegistryEntry]:
        """Return matching entries, ordered by agent name and version.

        ``tenant`` filters by declared visibility: entries with empty
        visibility are unrestricted, others match only when the tenant is
        listed. Stale (expired-lease) entries are included unless
        ``healthy_only``; entries past the stale-retention window are
        pruned on read.
        """
        now = time.monotonic()
        async with self._lock:
            self._entries = {
                key: entry
                for key, entry in self._entries.items()
                if now - entry.registered_at < _STALE_RETENTION_SECONDS or entry.healthy
            }
            entries = list(self._entries.values())
        if name is not None:
            entries = [e for e in entries if e.descriptor.name == name]
        if skill is not None:
            entries = [e for e in entries if any(s.id == skill for s in e.descriptor.skills)]
        if tenant is not None:
            entries = [
                e
                for e in entries
                if not e.descriptor.visibility or tenant in e.descriptor.visibility
            ]
        if healthy_only:
            entries = [e for e in entries if e.healthy]
        entries.sort(key=lambda e: (e.descriptor.name, e.descriptor.version))
        return entries

    async def get(self, name: str, version: str) -> RegistryEntry:
        async with self._lock:
            entry = self._entries.get((name, version))
            if entry is None:
                raise UnknownAgentError(f"{name}@{version} is not registered")
            return entry


class SqliteAgentRegistry:
    """SQLite-backed lease registry suitable for restart-safe deployments.

    Lease timestamps use wall-clock time because they cross process restarts;
    each loaded :class:`RegistryEntry` retains the same public health and age
    properties as the in-memory implementation. Writes are serialized with an
    immediate transaction and expired registrations remain queryable until
    the stale-retention window elapses.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        default_lease_seconds: float = DEFAULT_LEASE_SECONDS,
        stale_retention_seconds: float = _STALE_RETENTION_SECONDS,
    ) -> None:
        if default_lease_seconds <= 0 or stale_retention_seconds <= 0:
            raise ValueError("lease and stale-retention durations must be positive")
        self.path = str(path)
        self._default_lease = float(default_lease_seconds)
        self._stale_retention = float(stale_retention_seconds)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(self.path)
            self._initialize(self._memory_connection)
        else:
            path_obj = Path(self.path)
            if path_obj.parent != Path(""):
                path_obj.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as connection:
                self._initialize(connection)

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cloud_registry_entries (
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                descriptor_json TEXT NOT NULL,
                registered_at REAL NOT NULL,
                lease_expires_at REAL NOT NULL,
                PRIMARY KEY (name, version)
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_registry_retention
                ON cloud_registry_entries (registered_at);
            """
        )
        connection.commit()

    def _run(self, operation: Any) -> Any:
        with self._lock:
            if self._memory_connection is not None:
                return operation(self._memory_connection)
            with sqlite3.connect(self.path) as connection:
                return operation(connection)

    @staticmethod
    def _validate_descriptor(descriptor: AgentDescriptor) -> None:
        if not descriptor.name or not descriptor.version:
            raise DescriptorError("descriptor must carry a name and version")
        if descriptor.schema_version != AgentDescriptor().schema_version:
            raise DescriptorError(
                f"unsupported descriptor schema version '{descriptor.schema_version}'"
            )

    @staticmethod
    def _entry(row: sqlite3.Row | tuple[Any, ...]) -> RegistryEntry:
        descriptor_payload = json.loads(str(row[2]))
        descriptor = AgentDescriptor.from_dict(descriptor_payload)
        return RegistryEntry(
            descriptor=descriptor,
            registered_at=float(row[3]),
            lease_expires_at=float(row[4]),
            _clock=_wall_clock_now,
        )

    async def register(
        self, descriptor: AgentDescriptor, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        self._validate_descriptor(descriptor)
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        if ttl <= 0:
            raise DescriptorError("registration ttl must be positive")
        now = time.time()
        payload = json.dumps(descriptor.to_dict(), sort_keys=True, separators=(",", ":"))

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO cloud_registry_entries
                    (name, version, descriptor_json, registered_at, lease_expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (name, version) DO UPDATE SET
                    descriptor_json = excluded.descriptor_json,
                    registered_at = excluded.registered_at,
                    lease_expires_at = excluded.lease_expires_at
                """,
                (descriptor.name, descriptor.version, payload, now, now + float(ttl)),
            )
            connection.execute(
                "DELETE FROM cloud_registry_entries "
                "WHERE registered_at < ? AND lease_expires_at <= ?",
                (now - self._stale_retention, now),
            )
            connection.commit()

        await asyncio.to_thread(self._run, operation)
        return RegistryEntry(descriptor, now, now + float(ttl), _wall_clock_now)

    async def heartbeat(
        self, name: str, version: str, *, ttl_seconds: float | None = None
    ) -> RegistryEntry:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_lease
        if ttl <= 0:
            raise DescriptorError("heartbeat ttl must be positive")
        now = time.time()

        def operation(connection: sqlite3.Connection) -> tuple[Any, ...] | None:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT name, version, descriptor_json, registered_at, lease_expires_at "
                "FROM cloud_registry_entries WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE cloud_registry_entries SET lease_expires_at = ? "
                "WHERE name = ? AND version = ?",
                (now + float(ttl), name, version),
            )
            connection.commit()
            return (row[0], row[1], row[2], row[3], now + float(ttl))

        row = await asyncio.to_thread(self._run, operation)
        if row is None:
            raise UnknownAgentError(f"{name}@{version} is not registered")
        return self._entry(row)

    async def deregister(self, name: str, version: str) -> None:
        def operation(connection: sqlite3.Connection) -> bool:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM cloud_registry_entries WHERE name = ? AND version = ?",
                (name, version),
            )
            connection.commit()
            return cursor.rowcount > 0

        if not await asyncio.to_thread(self._run, operation):
            raise UnknownAgentError(f"{name}@{version} is not registered")

    async def query(
        self,
        *,
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> list[RegistryEntry]:
        now = time.time()

        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM cloud_registry_entries "
                "WHERE registered_at < ? AND lease_expires_at <= ?",
                (now - self._stale_retention, now),
            )
            rows = connection.execute(
                "SELECT name, version, descriptor_json, registered_at, lease_expires_at "
                "FROM cloud_registry_entries ORDER BY name, version"
            ).fetchall()
            connection.commit()
            return rows

        entries = [self._entry(row) for row in await asyncio.to_thread(self._run, operation)]
        if name is not None:
            entries = [entry for entry in entries if entry.descriptor.name == name]
        if skill is not None:
            entries = [
                entry for entry in entries if any(s.id == skill for s in entry.descriptor.skills)
            ]
        if tenant is not None:
            entries = [
                entry
                for entry in entries
                if not entry.descriptor.visibility or tenant in entry.descriptor.visibility
            ]
        if healthy_only:
            entries = [entry for entry in entries if entry.healthy]
        return entries

    async def get(self, name: str, version: str) -> RegistryEntry:
        def operation(connection: sqlite3.Connection) -> tuple[Any, ...] | None:
            return cast(
                tuple[Any, ...] | None,
                connection.execute(
                    "SELECT name, version, descriptor_json, registered_at, lease_expires_at "
                    "FROM cloud_registry_entries WHERE name = ? AND version = ?",
                    (name, version),
                ).fetchone(),
            )

        row = await asyncio.to_thread(self._run, operation)
        if row is None:
            raise UnknownAgentError(f"{name}@{version} is not registered")
        return self._entry(row)

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(
                self._run, lambda connection: connection.execute("SELECT 1").fetchone()
            )
            return True
        except sqlite3.Error:
            return False

    async def close(self) -> None:
        if self._memory_connection is not None:
            await asyncio.to_thread(self._memory_connection.close)
            self._memory_connection = None


def _entry_payload(entry: RegistryEntry) -> dict[str, Any]:
    return {
        "descriptor": entry.descriptor.to_dict(),
        "healthy": entry.healthy,
        "registered_at_age_seconds": round(entry.age_seconds(), 3),
        "lease_expires_in_seconds": round(entry.expires_in_seconds(), 3),
    }


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


def create_registry_app(
    registry: InMemoryAgentRegistry | SqliteAgentRegistry | None = None,
    *,
    database_path: str | Path | None = None,
) -> FastAPI:
    """Create the registry API with an in-memory or durable store."""
    if registry is not None and database_path is not None:
        raise ValueError("pass registry or database_path, not both")
    app = FastAPI(title="Micro-Agent Cloud Registry", version="0.1.0")
    owned = database_path is not None
    reg = (
        registry
        if registry is not None
        else (
            SqliteAgentRegistry(database_path)
            if database_path is not None
            else InMemoryAgentRegistry()
        )
    )
    app.state.registry = reg

    @app.put("/registry/agents/{name}/{version}")
    async def register_agent(
        name: str,
        version: str,
        payload: dict[str, Any],
        ttl_seconds: float | None = None,
    ) -> dict[str, Any]:
        body_name = str(payload.get("name", name))
        body_version = str(payload.get("version", version))
        if body_name != name or body_version != version:
            raise HTTPException(
                status_code=422,
                detail="descriptor identity must match the URL path name and version",
            )
        try:
            descriptor = AgentDescriptor.from_dict({**payload, "name": name, "version": version})
            entry = await reg.register(descriptor, ttl_seconds=ttl_seconds)
        except DescriptorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _entry_payload(entry)

    @app.post("/registry/agents/{name}/{version}/heartbeat")
    async def heartbeat(
        name: str, version: str, ttl_seconds: float | None = None
    ) -> dict[str, Any]:
        try:
            entry = await reg.heartbeat(name, version, ttl_seconds=ttl_seconds)
        except UnknownAgentError as exc:
            raise _not_found(exc) from exc
        except DescriptorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _entry_payload(entry)

    @app.delete("/registry/agents/{name}/{version}")
    async def deregister_agent(name: str, version: str) -> dict[str, Any]:
        try:
            await reg.deregister(name, version)
        except UnknownAgentError as exc:
            raise _not_found(exc) from exc
        return {"status": "deregistered", "name": name, "version": version}

    @app.get("/registry/agents")
    async def query_agents(
        name: str | None = None,
        skill: str | None = None,
        tenant: str | None = None,
        healthy_only: bool = False,
    ) -> dict[str, Any]:
        entries = await reg.query(name=name, skill=skill, tenant=tenant, healthy_only=healthy_only)
        return {"agents": [_entry_payload(entry) for entry in entries]}

    @app.get("/registry/agents/{name}/{version}")
    async def get_agent(name: str, version: str) -> dict[str, Any]:
        try:
            entry = await reg.get(name, version)
        except UnknownAgentError as exc:
            detail = str(exc.args[0] if exc.args else exc)
            raise HTTPException(status_code=404, detail=detail) from exc
        return _entry_payload(entry)

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        health_check = getattr(reg, "health_check", None)
        healthy = bool(await health_check()) if health_check is not None else True
        if not healthy:
            raise HTTPException(status_code=503, detail="registry store unavailable")
        return {"ready": True}

    if owned:

        async def close_owned_store() -> None:
            close = getattr(reg, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

        app.router.on_shutdown.append(close_owned_store)

    return app


def main() -> None:
    """Run the registry standalone: ``python -m cloud.registry``."""
    import uvicorn

    uvicorn.run(create_registry_app(), host="0.0.0.0", port=8090)


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "InMemoryAgentRegistry",
    "RegistryEntry",
    "SqliteAgentRegistry",
    "UnknownAgentError",
    "create_registry_app",
    "main",
]
