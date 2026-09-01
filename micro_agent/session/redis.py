"""Redis-backed session provider for shared deployments.

Redis provides cross-process storage and atomic pipelines for session writes.
The client is optional so the built-in in-memory and SQLite providers remain
usable without additional dependencies. Install ``micro-agents[redis]`` when
constructing this provider without an injected client.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
from datetime import datetime, timedelta
from types import ModuleType
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from micro_agent.session.session import (
    SessionContext,
    SessionMetadata,
    SessionProvider,
    _iso,
    _utc_now,
)
from micro_agent.state import StateConflictError


def _import_redis() -> ModuleType:
    """Import redis-py lazily so it stays an optional dependency."""
    try:
        redis = importlib.import_module("redis.asyncio")
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError(
            "redis-py is required for Redis sessions; install the optional "
            "'redis' extra ('micro-agents[redis]')"
        ) from exc
    return redis


class RedisSessionProvider(SessionProvider):
    """Session provider backed by Redis for independently scaled processes.

    Session documents are stored as JSON values and indexed in a sorted set.
    A transactional pipeline updates both keys together, while Redis key TTLs
    enforce expiry even when no process is reading a session. ``client`` is an
    injection seam for tests or deployments that own the Redis connection.
    """

    def __init__(
        self,
        endpoint: str = "redis://localhost:6379/0",
        *,
        ttl_seconds: int | None = None,
        namespace: str = "micro-agent",
        client: Any | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        _validate_endpoint(endpoint)
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 0
        ):
            raise ValueError("ttl_seconds must be a non-negative integer")
        if not namespace or namespace.strip() != namespace:
            raise ValueError("namespace must be a non-empty value without surrounding whitespace")

        self._endpoint = endpoint
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace
        self._prefix = f"{namespace}:session:"
        self._index_key = f"{namespace}:sessions"
        self._owns_client = client is None
        self._closed = False
        if client is None:
            redis = _import_redis()
            self._client = redis.from_url(
                endpoint,
                decode_responses=True,
                socket_connect_timeout=connect_timeout_seconds,
                socket_timeout=connect_timeout_seconds,
            )
        else:
            self._client = client

    def _key(self, session_id: str, tenant_id: str | None = None) -> str:
        if tenant_id is None:
            return f"{self._prefix}{session_id}"
        return f"{self._prefix}tenant:{quote(tenant_id, safe='')}:{session_id}"

    def _expiry(self, ttl_seconds: int | None, now: datetime) -> str | None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        if ttl is None:
            return None
        return _iso(now + timedelta(seconds=ttl))

    @staticmethod
    def _remaining_ttl(expires_at: str | None, now: datetime) -> int | None:
        if expires_at is None:
            return None
        try:
            remaining = (datetime.fromisoformat(expires_at) - now).total_seconds()
        except ValueError:
            return None
        if remaining <= 0:
            return 0
        return max(1, math.ceil(remaining))

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    @classmethod
    def _decode(cls, raw: Any) -> tuple[SessionContext, str, str | None]:
        data = json.loads(cls._text(raw))
        if not isinstance(data, dict) or "session_id" not in data:
            raise ValueError("Redis session payload is not an object")
        created_at = str(data.get("created_at") or _iso(_utc_now()))
        expires_at = data.get("expires_at")
        tenant_id = data.get("tenant_id")
        version = int(data.get("version") or 1)
        metadata = dict(data.get("metadata") or {})
        metadata["created_at"] = created_at
        if expires_at is not None:
            metadata["expires_at"] = expires_at
        return (
            SessionContext(
                session_id=str(data["session_id"]),
                tenant_id=str(tenant_id) if tenant_id is not None else None,
                version=version,
                messages=list(data.get("messages") or []),
                metadata=metadata,
                caller_context=dict(data.get("caller_context") or {}),
            ),
            created_at,
            expires_at,
        )

    @staticmethod
    def _encode(
        session: SessionContext,
        created_at: str,
        expires_at: str | None,
    ) -> str:
        metadata = {
            key: value
            for key, value in session.metadata.items()
            if key not in {"created_at", "expires_at"}
        }
        return json.dumps(
            {
                "session_id": session.session_id,
                "tenant_id": session.tenant_id,
                "version": session.version or 1,
                "messages": session.messages,
                "metadata": metadata,
                "caller_context": session.caller_context,
                "created_at": created_at,
                "expires_at": expires_at,
            },
            default=str,
        )

    async def _remove(self, session_id: str, tenant_id: str | None = None) -> None:
        key = self._key(session_id, tenant_id)
        pipeline = self._client.pipeline(transaction=True)
        pipeline.delete(key)
        pipeline.zrem(self._index_key, key)
        await pipeline.execute()

    async def _store(
        self, session: SessionContext, created_at: str, expires_at: str | None
    ) -> None:
        now = _utc_now()
        ttl = self._remaining_ttl(expires_at, now)
        key = self._key(session.session_id, session.tenant_id)
        pipeline = self._client.pipeline(transaction=True)
        if ttl == 0:
            pipeline.delete(key)
            pipeline.zrem(self._index_key, key)
        else:
            payload = self._encode(session, created_at, expires_at)
            if ttl is None:
                pipeline.set(key, payload)
            else:
                pipeline.set(key, payload, ex=ttl)
            score = 0.0 if expires_at is None else datetime.fromisoformat(expires_at).timestamp()
            pipeline.zadd(self._index_key, {key: score})
        await pipeline.execute()

    async def create(
        self,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
        *,
        tenant_id: str | None = None,
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        created_at = _iso(now)
        expires_at = self._expiry(ttl_seconds, now)
        context = SessionContext(session_id=sid, tenant_id=tenant_id, version=1)
        context.metadata["created_at"] = created_at
        if expires_at is not None:
            context.metadata["expires_at"] = expires_at
        await self._store(context, created_at, expires_at)
        return context

    async def get(self, session_id: str, *, tenant_id: str | None = None) -> SessionContext | None:
        key = self._key(session_id, tenant_id)
        raw = await self._client.get(key)
        if raw is None:
            # The Redis TTL may have removed the document while its index
            # member remains; clean that stale index entry opportunistically.
            await self._client.zrem(self._index_key, key)
            return None
        try:
            context, _created_at, expires_at = self._decode(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self._remove(session_id, tenant_id)
            return None
        if expires_at is not None:
            try:
                expired = _utc_now() >= datetime.fromisoformat(expires_at)
            except ValueError:
                expired = False
            if expired:
                await self._remove(session_id, tenant_id)
                return None
        return context

    async def update(
        self,
        session: SessionContext,
        ttl_seconds: int | None = None,
        *,
        expected_version: int | None = None,
    ) -> None:
        key = self._key(session.session_id, session.tenant_id)
        expected = expected_version if expected_version is not None else session.version

        def prepare(existing: Any) -> tuple[str, str | None, int]:
            created_at = str(session.metadata.get("created_at") or _iso(_utc_now()))
            existing_expires: str | None = None
            actual_version = 0
            if existing is not None:
                try:
                    existing_context, existing_created_at, existing_expires = self._decode(existing)
                    actual_version = existing_context.version or 1
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    existing_expires = None
                else:
                    if "created_at" not in session.metadata:
                        created_at = existing_created_at
            if actual_version == 0:
                if expected not in (0, None):
                    raise StateConflictError("session", session.session_id, expected, 0)
                new_version = 1
            else:
                if expected and expected != actual_version:
                    raise StateConflictError(
                        "session", session.session_id, expected, actual_version
                    )
                new_version = actual_version + 1
            expires_at = (
                self._expiry(ttl_seconds, _utc_now())
                if ttl_seconds is not None
                else existing_expires
            )
            return created_at, expires_at, new_version

        for _attempt in range(3):
            pipeline = self._client.pipeline(transaction=True)
            watch = getattr(pipeline, "watch", None)
            if not callable(watch):
                existing = await self._client.get(key)
                created_at, expires_at, new_version = prepare(existing)
                session.version = new_version
                if expires_at is not None:
                    session.metadata["expires_at"] = expires_at
                await self._store(session, created_at, expires_at)
                return
            try:
                watched = watch(key)
                if inspect.isawaitable(watched):
                    await watched
                existing = pipeline.get(key)
                if inspect.isawaitable(existing):
                    existing = await existing
                created_at, expires_at, new_version = prepare(existing)
                session.version = new_version
                if expires_at is not None:
                    session.metadata["expires_at"] = expires_at
                pipeline.multi()
                ttl = self._remaining_ttl(expires_at, _utc_now())
                if ttl == 0:
                    pipeline.delete(key)
                    pipeline.zrem(self._index_key, key)
                else:
                    payload = self._encode(session, created_at, expires_at)
                    if ttl is None:
                        pipeline.set(key, payload)
                    else:
                        pipeline.set(key, payload, ex=ttl)
                    score = (
                        0.0
                        if expires_at is None
                        else datetime.fromisoformat(expires_at).timestamp()
                    )
                    pipeline.zadd(self._index_key, {key: score})
                await pipeline.execute()
                return
            except Exception as exc:
                if exc.__class__.__name__ != "WatchError":
                    raise
            finally:
                reset = getattr(pipeline, "reset", None)
                if callable(reset):
                    result = reset()
                    if inspect.isawaitable(result):
                        await result
        raise StateConflictError("session", session.session_id, expected or 0, -1)

    async def delete(self, session_id: str, *, tenant_id: str | None = None) -> None:
        await self._remove(session_id, tenant_id)

    async def list_active(self, *, tenant_id: str | None = None) -> list[SessionMetadata]:
        members = await self._client.zrange(self._index_key, 0, -1)
        if not members:
            return []
        keys = [self._text(member) for member in members]
        raw_values = await self._client.mget(keys)
        active: list[SessionMetadata] = []
        stale: list[str] = []
        now = _utc_now()
        for key, raw in zip(keys, raw_values, strict=False):
            if raw is None:
                stale.append(key)
                continue
            try:
                context, created_at, expires_at = self._decode(raw)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                stale.append(key)
                continue
            if tenant_id is not None and context.tenant_id != tenant_id:
                continue
            if expires_at is not None:
                try:
                    if now >= datetime.fromisoformat(expires_at):
                        stale.append(key)
                        continue
                except ValueError:
                    pass
            active.append(
                SessionMetadata(
                    session_id=context.session_id,
                    tenant_id=context.tenant_id,
                    version=context.version,
                    created_at=created_at,
                    expires_at=expires_at,
                )
            )
        if stale:
            pipeline = self._client.pipeline(transaction=True)
            for key in stale:
                pipeline.delete(key)
                pipeline.zrem(self._index_key, key)
            await pipeline.execute()
        return active

    async def health_check(self) -> bool:
        """Return whether Redis answers a ping probe."""
        result = await self._client.ping()
        return bool(result)

    async def aclose(self) -> None:
        """Close an owned Redis client; injected clients remain caller-owned."""
        if self._closed or not self._owns_client:
            self._closed = True
            return
        close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result
        self._closed = True


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
        raise ValueError("Redis session endpoint must use redis:// or rediss:// with a host")
