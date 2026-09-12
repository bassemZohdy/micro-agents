"""Durable A2A task and push-notification stores.

The official A2A SDK intentionally exposes small storage interfaces.  This
module keeps the persistence implementation independent from the SDK at
import time, so the base HTTP package remains usable without the optional
``a2a`` extra.  SQLite is the portable reference backend; deployments can
replace it with a store implementing the same ``save/get/delete`` methods.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

_DEFAULT_TENANT = "__default__"
_DEFAULT_TASK_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_MAX_TASK_BYTES = 2 * 1024 * 1024


def _tenant_from_context(context: Any) -> str:
    """Extract a verified tenant from an SDK call context.

    A2A's ``ServerCallContext`` deliberately permits application state.  The
    HTTP boundary stores the verified tenant there; untrusted request metadata
    is never consulted.  Context-less SDK calls use a separate default scope.
    """
    state = getattr(context, "state", None)
    if isinstance(state, Mapping):
        tenant = state.get("tenant_id") or state.get("tenant")
        if tenant:
            return str(tenant)
    user = getattr(context, "user", None)
    tenant = getattr(user, "tenant_id", None)
    if tenant:
        return str(tenant)
    return _DEFAULT_TENANT


def _connect(path: str, *, uri: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0, uri=uri)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class SqliteA2ATaskStore:
    """SQLite-backed implementation of the A2A SDK ``TaskStore`` SPI.

    Task JSON is stored as one bounded document so SDK upgrades do not require
    this package to mirror every A2A nested model.  Rows are keyed by tenant
    and task ID, expired rows are removed on access, and writes use an
    immediate transaction so independent application processes do not race
    through a partial update.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        ttl_seconds: float = _DEFAULT_TASK_TTL_SECONDS,
        max_task_bytes: int = _DEFAULT_MAX_TASK_BYTES,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if max_task_bytes < 1024:
            raise ValueError("max_task_bytes must be at least 1024")
        self.path = str(path)
        self.ttl_seconds = float(ttl_seconds)
        self.max_task_bytes = max_task_bytes
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = _connect(self.path)
            self._initialize(self._memory_connection)
        else:
            path_obj = Path(self.path)
            if path_obj.parent != Path(""):
                path_obj.parent.mkdir(parents=True, exist_ok=True)
            with _connect(self.path) as connection:
                self._initialize(connection)

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS a2a_tasks (
                tenant_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                context_id TEXT NOT NULL,
                task_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_a2a_tasks_expiry
                ON a2a_tasks (expires_at);
            """
        )
        connection.commit()

    def _run(self, operation: Any) -> Any:
        with self._lock:
            if self._memory_connection is not None:
                return operation(self._memory_connection)
            with _connect(self.path) as connection:
                return operation(connection)

    async def _run_async(self, operation: Any) -> Any:
        return await asyncio.to_thread(self._run, operation)

    @staticmethod
    def _task_json(task: Any) -> str:
        dump = getattr(task, "model_dump", None)
        if not callable(dump):
            raise TypeError("A2A task must provide model_dump()")
        return json.dumps(
            dump(by_alias=True, mode="json", exclude_none=False), separators=(",", ":")
        )

    async def save(self, task: Any, context: Any = None) -> None:
        """Persist or replace one task snapshot."""
        task_json = self._task_json(task)
        if len(task_json.encode("utf-8")) > self.max_task_bytes:
            raise ValueError("A2A task exceeds the configured persistence limit")
        tenant_id = _tenant_from_context(context)
        task_id = str(getattr(task, "id", ""))
        context_id = str(getattr(task, "context_id", ""))
        if not task_id or not context_id:
            raise ValueError("A2A tasks require non-empty id and context_id")
        now = time.time()
        expires_at = now + self.ttl_seconds

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO a2a_tasks
                    (tenant_id, task_id, context_id, task_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, task_id) DO UPDATE SET
                    context_id = excluded.context_id,
                    task_json = excluded.task_json,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, task_id, context_id, task_json, now, now, expires_at),
            )
            connection.execute("DELETE FROM a2a_tasks WHERE expires_at <= ?", (now,))
            connection.commit()

        await self._run_async(operation)

    async def get(self, task_id: str, context: Any = None) -> Any | None:
        """Load a non-expired task, rebuilding the SDK model lazily."""
        tenant_id = _tenant_from_context(context)
        now = time.time()

        def operation(connection: sqlite3.Connection) -> str | None:
            row = connection.execute(
                """
                SELECT task_json
                FROM a2a_tasks
                WHERE tenant_id = ? AND task_id = ? AND expires_at > ?
                """,
                (tenant_id, task_id, now),
            ).fetchone()
            if row is None:
                connection.execute(
                    "DELETE FROM a2a_tasks WHERE tenant_id = ? AND task_id = ?",
                    (tenant_id, task_id),
                )
                connection.commit()
                return None
            return str(row["task_json"])

        task_json = await self._run_async(operation)
        if task_json is None:
            return None
        try:
            from a2a.types import Task
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("SqliteA2ATaskStore requires the 'a2a' extra") from exc
        return Task.model_validate(json.loads(task_json))

    async def delete(self, task_id: str, context: Any = None) -> None:
        tenant_id = _tenant_from_context(context)

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM a2a_tasks WHERE tenant_id = ? AND task_id = ?",
                (tenant_id, task_id),
            )
            connection.commit()

        await self._run_async(operation)

    async def purge_expired(self) -> int:
        """Delete expired tasks and return the number of removed rows."""
        now = time.time()

        def operation(connection: sqlite3.Connection) -> int:
            cursor = connection.execute("DELETE FROM a2a_tasks WHERE expires_at <= ?", (now,))
            connection.commit()
            return int(cursor.rowcount)

        return int(await self._run_async(operation))

    async def health_check(self) -> bool:
        """Return whether the store can execute a read transaction."""

        def operation(connection: sqlite3.Connection) -> bool:
            connection.execute("SELECT 1").fetchone()
            return True

        try:
            return bool(await self._run_async(operation))
        except sqlite3.Error:
            return False

    async def close(self) -> None:
        """Close the in-memory connection, if this store owns one."""
        if self._memory_connection is not None:
            await asyncio.to_thread(self._memory_connection.close)
            self._memory_connection = None


class SqlitePushNotificationConfigStore:
    """Durable A2A push callback configuration store."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = _connect(self.path)
            self._initialize(self._memory_connection)
        else:
            path_obj = Path(self.path)
            if path_obj.parent != Path(""):
                path_obj.parent.mkdir(parents=True, exist_ok=True)
            with _connect(self.path) as connection:
                self._initialize(connection)

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS a2a_push_configs (
                task_id TEXT NOT NULL,
                config_id TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (task_id, config_id)
            )
            """
        )
        connection.commit()

    def _run(self, operation: Any) -> Any:
        with self._lock:
            if self._memory_connection is not None:
                return operation(self._memory_connection)
            with _connect(self.path) as connection:
                return operation(connection)

    async def set_info(self, task_id: str, notification_config: Any) -> None:
        dump = getattr(notification_config, "model_dump", None)
        if not callable(dump):
            raise TypeError("notification_config must provide model_dump()")
        config_id = str(getattr(notification_config, "id", None) or uuid4())
        payload = json.dumps(dump(by_alias=True, mode="json", exclude_none=False))
        now = time.time()

        def operation(connection: sqlite3.Connection) -> None:
            connection.execute(
                """
                INSERT INTO a2a_push_configs
                    (task_id, config_id, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (task_id, config_id) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (task_id, config_id, payload, now, now),
            )
            connection.commit()

        await asyncio.to_thread(self._run, operation)

    async def get_info(self, task_id: str) -> list[Any]:
        def operation(connection: sqlite3.Connection) -> list[str]:
            return [
                str(row["config_json"])
                for row in connection.execute(
                    """
                    SELECT config_json
                    FROM a2a_push_configs
                    WHERE task_id = ? ORDER BY created_at
                    """,
                    (task_id,),
                ).fetchall()
            ]

        rows = await asyncio.to_thread(self._run, operation)
        try:
            from a2a.types import PushNotificationConfig
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("push notification storage requires the 'a2a' extra") from exc
        return [PushNotificationConfig.model_validate(json.loads(payload)) for payload in rows]

    async def delete_info(self, task_id: str, config_id: str | None = None) -> None:
        def operation(connection: sqlite3.Connection) -> None:
            if config_id:
                connection.execute(
                    "DELETE FROM a2a_push_configs WHERE task_id = ? AND config_id = ?",
                    (task_id, config_id),
                )
            else:
                connection.execute("DELETE FROM a2a_push_configs WHERE task_id = ?", (task_id,))
            connection.commit()

        await asyncio.to_thread(self._run, operation)

    async def close(self) -> None:
        if self._memory_connection is not None:
            await asyncio.to_thread(self._memory_connection.close)
            self._memory_connection = None


class HttpxPushNotificationSender:
    """Bounded, authenticated HTTPS sender for A2A task notifications."""

    def __init__(
        self,
        config_store: Any,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.1,
        allow_local_http: bool = True,
        allowed_hosts: set[str] | None = None,
        max_payload_bytes: int = _DEFAULT_MAX_TASK_BYTES,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if max_payload_bytes < 1024:
            raise ValueError("max_payload_bytes must be at least 1024")
        self._config_store = config_store
        self._client = client or httpx.AsyncClient(timeout=10.0, trust_env=False)
        self._owns_client = client is None
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._allow_local_http = allow_local_http
        self._allowed_hosts = {host.lower() for host in allowed_hosts} if allowed_hosts else None
        self._max_payload_bytes = max_payload_bytes

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("push notification URL must be an absolute HTTP(S) URL")
        host = (parsed.hostname or "").lower()
        local = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (self._allow_local_http and local):
            raise ValueError("push notification callbacks must use HTTPS")
        if self._allowed_hosts is not None and host not in self._allowed_hosts:
            raise ValueError("push notification callback host is not allowed")

    @staticmethod
    def _headers(config: Any) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = getattr(config, "token", None)
        if token:
            headers["X-A2A-Notification-Token"] = str(token)
        authentication = getattr(config, "authentication", None)
        schemes = {str(scheme).lower() for scheme in (getattr(authentication, "schemes", []) or [])}
        credentials = getattr(authentication, "credentials", None)
        if credentials and "bearer" in schemes:
            headers["Authorization"] = f"Bearer {credentials}"
        elif credentials:
            headers["X-A2A-Notification-Credentials"] = str(credentials)
        return headers

    async def send_notification(self, task: Any) -> None:
        dump = getattr(task, "model_dump", None)
        if not callable(dump):
            raise TypeError("task must provide model_dump()")
        payload = json.dumps(
            dump(by_alias=True, mode="json", exclude_none=False), separators=(",", ":")
        )
        if len(payload.encode("utf-8")) > self._max_payload_bytes:
            raise ValueError("A2A notification payload exceeds the configured limit")
        for config in await self._config_store.get_info(str(task.id)):
            self._validate_url(str(config.url))
            headers = self._headers(config)
            for attempt in range(self._max_attempts):
                try:
                    response = await self._client.post(
                        str(config.url), content=payload, headers=headers
                    )
                    if response.status_code < 400:
                        break
                    if response.status_code < 500 and response.status_code != 429:
                        break
                except httpx.TransportError:
                    if attempt + 1 == self._max_attempts:
                        break
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(self._backoff_seconds * (2**attempt))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "HttpxPushNotificationSender",
    "SqliteA2ATaskStore",
    "SqlitePushNotificationConfigStore",
]
