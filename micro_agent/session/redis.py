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
from urllib.parse import urlsplit
from uuid import uuid4

from micro_agent.session.session import (
    SessionContext,
    SessionMetadata,
    SessionProvider,
    _iso,
    _utc_now,
)


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

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

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
        metadata = dict(data.get("metadata") or {})
        metadata["created_at"] = created_at
        if expires_at is not None:
            metadata["expires_at"] = expires_at
        return (
            SessionContext(
                session_id=str(data["session_id"]),
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
                "messages": session.messages,
                "metadata": metadata,
                "caller_context": session.caller_context,
                "created_at": created_at,
                "expires_at": expires_at,
            },
            default=str,
        )

    async def _remove(self, session_id: str) -> None:
        pipeline = self._client.pipeline(transaction=True)
        pipeline.delete(self._key(session_id))
        pipeline.zrem(self._index_key, self._key(session_id))
        await pipeline.execute()

    async def _store(
        self, session: SessionContext, created_at: str, expires_at: str | None
    ) -> None:
        now = _utc_now()
        ttl = self._remaining_ttl(expires_at, now)
        key = self._key(session.session_id)
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
        self, session_id: str | None = None, ttl_seconds: int | None = None
    ) -> SessionContext:
        sid = session_id or str(uuid4())
        now = _utc_now()
        created_at = _iso(now)
        expires_at = self._expiry(ttl_seconds, now)
        context = SessionContext(session_id=sid)
        context.metadata["created_at"] = created_at
        if expires_at is not None:
            context.metadata["expires_at"] = expires_at
        await self._store(context, created_at, expires_at)
        return context

    async def get(self, session_id: str) -> SessionContext | None:
        key = self._key(session_id)
        raw = await self._client.get(key)
        if raw is None:
            # The Redis TTL may have removed the document while its index
            # member remains; clean that stale index entry opportunistically.
            await self._client.zrem(self._index_key, key)
            return None
        try:
            context, _created_at, expires_at = self._decode(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self._remove(session_id)
            return None
        if expires_at is not None:
            try:
                expired = _utc_now() >= datetime.fromisoformat(expires_at)
            except ValueError:
                expired = False
            if expired:
                await self._remove(session_id)
                return None
        return context

    async def update(self, session: SessionContext, ttl_seconds: int | None = None) -> None:
        key = self._key(session.session_id)
        existing = await self._client.get(key)
        created_at = str(session.metadata.get("created_at") or _iso(_utc_now()))
        existing_expires: str | None = None
        if existing is not None:
            try:
                _existing_context, existing_created_at, existing_expires = self._decode(existing)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                existing_expires = None
            else:
                if "created_at" not in session.metadata:
                    created_at = existing_created_at
        expires_at = (
            self._expiry(ttl_seconds, _utc_now()) if ttl_seconds is not None else existing_expires
        )
        if expires_at is not None:
            session.metadata["expires_at"] = expires_at
        await self._store(session, created_at, expires_at)

    async def delete(self, session_id: str) -> None:
        await self._remove(session_id)

    async def list_active(self) -> list[SessionMetadata]:
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
