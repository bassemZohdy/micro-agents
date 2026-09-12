"""Versioned distributed configuration for Micro-Agent Cloud (C2).

Stores definitions and environment overlays as immutable, monotonic versions
per agent. Agents pin a version at start and keep it across config-plane
outages (the C0 stance: the config plane rolls new versions, it never
mutates a running agent). Definitions are validated with the core's own
loader and overlays with the core's :class:`EnvironmentOverlay`, so the
config plane can never store something an agent would fail to boot.

Secrets stay references: definitions carry ``credential_ref`` fields and the
config plane stores exactly what was validated — never resolved values. The
:class:`SecretResolver` protocol (with an environment-variable
implementation) resolves references at use time, integrating existing
secret-management systems without the store ever holding secret material.

The in-memory store remains the lightweight default. ``SqliteConfigStore``
provides a restart-safe reference backend with optional retention; deployments
can replace it with a shared database implementation without changing the
HTTP contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from cloud.auth import PlaneAuthenticator, install_plane_auth
from cloud.schemas import ConfigRecordContract
from micro_agent.config import EnvironmentOverlay
from micro_agent.definition import load_definition_from_dict


class ConfigValidationError(ValueError):
    """Raised when a definition or overlay fails the core's validation."""


@dataclass
class ConfigRecord:
    """One immutable configuration version."""

    agent: str
    kind: str  # "definition" or "overlay"
    version: int
    payload: dict[str, Any]
    digest: str
    created_at: float


class SecretResolver(Protocol):
    """Resolves credential references to secret values at use time."""

    def resolve(self, reference: str) -> str | None:
        """Return the secret for ``reference`` or ``None`` when absent."""


class EnvironmentSecretResolver:
    """Reads secret references from environment variables.

    The reference is the variable name; values are looked up only when a
    deployment actually needs them, keeping the config plane free of secret
    material. Other resolvers (Vault, cloud secret managers) implement the
    same one-method protocol.
    """

    def resolve(self, reference: str) -> str | None:
        return os.environ.get(reference)


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class InMemoryConfigStore:
    """Append-only versioned definitions and overlays, per agent."""

    def __init__(self) -> None:
        self._history: dict[tuple[str, str], list[ConfigRecord]] = {}
        self._lock = asyncio.Lock()

    async def store_definition(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        try:
            load_definition_from_dict(payload)
        except Exception as exc:  # noqa: BLE001 — surface any validation failure as 422
            raise ConfigValidationError(f"invalid definition: {exc}") from exc
        return await self._store(agent, "definition", payload)

    async def store_overlay(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        try:
            EnvironmentOverlay.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — surface any validation failure as 422
            raise ConfigValidationError(f"invalid overlay: {exc}") from exc
        return await self._store(agent, "overlay", payload)

    async def _store(self, agent: str, kind: str, payload: dict[str, Any]) -> ConfigRecord:
        if not agent:
            raise ConfigValidationError("agent name must not be empty")
        async with self._lock:
            versions = self._history.setdefault((agent, kind), [])
            record = ConfigRecord(
                agent=agent,
                kind=kind,
                version=len(versions) + 1,
                payload=payload,
                digest=_digest(payload),
                created_at=time.time(),
            )
            versions.append(record)
            return record

    async def get(self, agent: str, kind: str, version: int | None = None) -> ConfigRecord:
        """Return a pinned version, or the latest when ``version`` is None."""
        async with self._lock:
            versions = self._history.get((agent, kind), [])
            if version is None:
                if not versions:
                    raise KeyError(f"{agent} has no stored {kind}")
                return versions[-1]
            for record in versions:
                if record.version == version:
                    return record
            raise KeyError(f"{agent} has no {kind} version {version}")

    async def history(self, agent: str, kind: str) -> list[ConfigRecord]:
        """All versions oldest-first; empty when the agent is unknown."""
        async with self._lock:
            return list(self._history.get((agent, kind), []))

    async def rollback(self, agent: str, kind: str, to_version: int) -> ConfigRecord:
        """Roll back by storing the old content as a brand-new version.

        History is append-only: a rollback never rewrites or removes
        versions, so the previous lineage stays auditable and the new
        version is what freshly starting agents pin.
        """
        target = await self.get(agent, kind, to_version)
        return await self._store(agent, kind, dict(target.payload))


class SqliteConfigStore:
    """Restart-safe append-only configuration store.

    Payloads are validated with the same core contracts as the in-memory
    implementation. Each agent/kind has a monotonic version, and optional
    retention removes old records only after a newer version is committed.
    """

    def __init__(self, path: str | Path, *, retention_seconds: float | None = None) -> None:
        if retention_seconds is not None and retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive when provided")
        self.path = str(path)
        self.retention_seconds = retention_seconds
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
            CREATE TABLE IF NOT EXISTS cloud_config_records (
                agent TEXT NOT NULL,
                kind TEXT NOT NULL,
                version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                digest TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (agent, kind, version)
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_config_lookup
                ON cloud_config_records (agent, kind, version DESC);
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
    def _validate(agent: str, kind: str, payload: dict[str, Any]) -> None:
        if not agent:
            raise ConfigValidationError("agent name must not be empty")
        if kind == "definition":
            try:
                load_definition_from_dict(payload)
            except Exception as exc:  # noqa: BLE001 — normalize validation boundary
                raise ConfigValidationError(f"invalid definition: {exc}") from exc
        elif kind == "overlay":
            try:
                EnvironmentOverlay.model_validate(payload)
            except Exception as exc:  # noqa: BLE001 — normalize validation boundary
                raise ConfigValidationError(f"invalid overlay: {exc}") from exc
        else:
            raise ConfigValidationError("kind must be definition or overlay")

    @staticmethod
    def _record(row: tuple[Any, ...]) -> ConfigRecord:
        return ConfigRecord(
            agent=str(row[0]),
            kind=str(row[1]),
            version=int(row[2]),
            payload=json.loads(str(row[3])),
            digest=str(row[4]),
            created_at=float(row[5]),
        )

    async def store_definition(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        return await self._store(agent, "definition", payload)

    async def store_overlay(self, agent: str, payload: dict[str, Any]) -> ConfigRecord:
        return await self._store(agent, "overlay", payload)

    async def _store(self, agent: str, kind: str, payload: dict[str, Any]) -> ConfigRecord:
        self._validate(agent, kind, payload)
        now = time.time()
        payload_copy = json.loads(json.dumps(payload, default=str))
        payload_json = json.dumps(payload_copy, sort_keys=True, separators=(",", ":"))

        def operation(connection: sqlite3.Connection) -> tuple[Any, ...]:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM cloud_config_records "
                "WHERE agent = ? AND kind = ?",
                (agent, kind),
            ).fetchone()
            version = int(row[0]) + 1
            digest = _digest(payload_copy)
            connection.execute(
                "INSERT INTO cloud_config_records "
                "(agent, kind, version, payload_json, digest, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent, kind, version, payload_json, digest, now),
            )
            if self.retention_seconds is not None:
                connection.execute(
                    "DELETE FROM cloud_config_records WHERE agent = ? AND kind = ? "
                    "AND created_at < ? AND version != ?",
                    (agent, kind, now - self.retention_seconds, version),
                )
            connection.commit()
            return (agent, kind, version, payload_json, digest, now)

        return self._record(await asyncio.to_thread(self._run, operation))

    async def get(self, agent: str, kind: str, version: int | None = None) -> ConfigRecord:
        if not agent:
            raise ConfigValidationError("agent name must not be empty")
        if kind not in {"definition", "overlay"}:
            raise ConfigValidationError("kind must be definition or overlay")

        def operation(connection: sqlite3.Connection) -> tuple[Any, ...] | None:
            if version is None:
                return cast(
                    tuple[Any, ...] | None,
                    connection.execute(
                        "SELECT agent, kind, version, payload_json, digest, created_at "
                        "FROM cloud_config_records WHERE agent = ? AND kind = ? "
                        "ORDER BY version DESC LIMIT 1",
                        (agent, kind),
                    ).fetchone(),
                )
            return cast(
                tuple[Any, ...] | None,
                connection.execute(
                    "SELECT agent, kind, version, payload_json, digest, created_at "
                    "FROM cloud_config_records WHERE agent = ? AND kind = ? AND version = ?",
                    (agent, kind, version),
                ).fetchone(),
            )

        row = await asyncio.to_thread(self._run, operation)
        if row is None:
            if version is None:
                raise KeyError(f"{agent} has no stored {kind}")
            raise KeyError(f"{agent} has no {kind} version {version}")
        return self._record(row)

    async def history(self, agent: str, kind: str) -> list[ConfigRecord]:
        def operation(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
            return connection.execute(
                "SELECT agent, kind, version, payload_json, digest, created_at "
                "FROM cloud_config_records WHERE agent = ? AND kind = ? ORDER BY version",
                (agent, kind),
            ).fetchall()

        return [self._record(row) for row in await asyncio.to_thread(self._run, operation)]

    async def rollback(self, agent: str, kind: str, to_version: int) -> ConfigRecord:
        target = await self.get(agent, kind, to_version)
        return await self._store(agent, kind, dict(target.payload))

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


def _record_payload(record: ConfigRecord, include_payload: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "agent": record.agent,
        "kind": record.kind,
        "version": record.version,
        "digest": record.digest,
        "created_at": record.created_at,
    }
    if include_payload:
        body["payload"] = record.payload
    return ConfigRecordContract.model_validate(body).model_dump(exclude_none=True)


def create_config_app(
    store: InMemoryConfigStore | SqliteConfigStore | None = None,
    *,
    database_path: str | Path | None = None,
    authenticator: PlaneAuthenticator | None = None,
) -> Any:
    """Create the config-plane API with an in-memory or durable store."""
    if store is not None and database_path is not None:
        raise ValueError("pass store or database_path, not both")
    from fastapi import FastAPI, HTTPException

    app = FastAPI(title="Micro-Agent Cloud Config", version="0.1.0")
    owned = database_path is not None
    cfg = (
        store
        if store is not None
        else (
            SqliteConfigStore(database_path) if database_path is not None else InMemoryConfigStore()
        )
    )
    app.state.config_store = cfg
    install_plane_auth(app, authenticator)

    def _not_found(exc: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))

    @app.put("/config/agents/{agent}/definition")
    async def put_definition(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = await cfg.store_definition(agent, payload)
        except ConfigValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _record_payload(record, include_payload=False)

    @app.put("/config/agents/{agent}/overlay")
    async def put_overlay(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = await cfg.store_overlay(agent, payload)
        except ConfigValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _record_payload(record, include_payload=False)

    @app.get("/config/agents/{agent}/definition")
    async def get_definition(agent: str, version: int | None = None) -> dict[str, Any]:
        try:
            record = await cfg.get(agent, "definition", version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=True)

    @app.get("/config/agents/{agent}/overlay")
    async def get_overlay(agent: str, version: int | None = None) -> dict[str, Any]:
        try:
            record = await cfg.get(agent, "overlay", version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=True)

    @app.get("/config/agents/{agent}/history")
    async def get_history(agent: str, kind: str = "definition") -> dict[str, Any]:
        if kind not in {"definition", "overlay"}:
            raise HTTPException(status_code=422, detail="kind must be definition or overlay")
        records = await cfg.history(agent, kind)
        return {"versions": [_record_payload(r, include_payload=False) for r in records]}

    @app.post("/config/agents/{agent}/rollback")
    async def post_rollback(agent: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(payload.get("kind", "definition"))
        to_version = payload.get("to_version")
        if kind not in {"definition", "overlay"}:
            raise HTTPException(status_code=422, detail="kind must be definition or overlay")
        if not isinstance(to_version, int):
            raise HTTPException(status_code=422, detail="to_version must be an integer")
        try:
            record = await cfg.rollback(agent, kind, to_version)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _record_payload(record, include_payload=False)

    @app.get("/health/ready")
    async def ready() -> dict[str, bool]:
        health_check = getattr(cfg, "health_check", None)
        healthy = bool(await health_check()) if health_check is not None else True
        if not healthy:
            raise HTTPException(status_code=503, detail="config store unavailable")
        return {"ready": True}

    if owned:

        async def close_owned_store() -> None:
            close = getattr(cfg, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result

        app.router.on_shutdown.append(close_owned_store)

    return app


__all__ = [
    "ConfigRecord",
    "ConfigValidationError",
    "EnvironmentSecretResolver",
    "InMemoryConfigStore",
    "SqliteConfigStore",
    "SecretResolver",
    "create_config_app",
]
