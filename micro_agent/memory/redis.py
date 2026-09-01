"""Redis-backed memory provider for shared deployments."""

from __future__ import annotations

import inspect
import json
import math
from datetime import datetime, timedelta
from typing import Any

from micro_agent.memory.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.session.redis import _import_redis, _validate_endpoint
from micro_agent.session.session import _iso, _utc_now


class RedisMemoryProvider(MemoryProvider):
    """Memory provider backed by Redis with scope-aware JSON records.

    Records are indexed in a sorted set by storage time. Redis key TTLs enforce
    expiration independently of readers; stale index members are removed on
    reads and before capacity eviction. ``client`` is injectable for tests or
    deployments that own the Redis connection.
    """

    def __init__(
        self,
        endpoint: str = "redis://localhost:6379/0",
        *,
        policy: MemoryPolicy | None = None,
        namespace: str = "micro-agent",
        client: Any | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        _validate_endpoint(endpoint)
        if not namespace or namespace.strip() != namespace:
            raise ValueError("namespace must be a non-empty value without surrounding whitespace")
        self._endpoint = endpoint
        self.policy = policy or MemoryPolicy()
        self._namespace = namespace
        self._prefix = f"{namespace}:memory:"
        self._index_key = f"{namespace}:memory:index"
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

    @staticmethod
    def _scope(scope: str | None) -> str:
        return scope or "agent"

    def _key(self, key: str, scope: str | None) -> str:
        return f"{self._prefix}{self._scope(scope)}:{key}"

    def _expiry(self, now: datetime) -> str | None:
        ttl = self.policy.ttl_seconds
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
    def _decode(cls, raw: Any) -> MemoryEntry:
        data = json.loads(cls._text(raw))
        if not isinstance(data, dict) or "key" not in data or "scope" not in data:
            raise ValueError("Redis memory payload is not an object")
        metadata = dict(data.get("metadata") or {})
        stored_at = data.get("stored_at")
        expires_at = data.get("expires_at")
        if stored_at is not None:
            metadata.setdefault("stored_at", stored_at)
        if expires_at is not None:
            metadata.setdefault("expires_at", expires_at)
        return MemoryEntry(
            key=str(data["key"]),
            value=data.get("value"),
            scope=str(data["scope"]),
            metadata=metadata,
        )

    @staticmethod
    def _record(entry: MemoryEntry, now: datetime, expires_at: str | None) -> str:
        metadata = {
            key: value
            for key, value in entry.metadata.items()
            if key not in {"stored_at", "expires_at"}
        }
        return json.dumps(
            {
                "key": entry.key,
                "value": entry.value,
                "scope": entry.scope,
                "metadata": metadata,
                "stored_at": _iso(now),
                "expires_at": expires_at,
            },
            default=str,
        )

    @classmethod
    def _expired(cls, raw: Any, now: datetime) -> bool:
        try:
            data = json.loads(cls._text(raw))
            expires_at = data.get("expires_at") if isinstance(data, dict) else None
            return expires_at is not None and now >= datetime.fromisoformat(expires_at)
        except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
            return True

    async def _remove_key(self, key: str) -> None:
        pipeline = self._client.pipeline(transaction=True)
        pipeline.delete(key)
        pipeline.zrem(self._index_key, key)
        await pipeline.execute()

    async def _purge_stale(self) -> None:
        members = await self._client.zrange(self._index_key, 0, -1)
        if not members:
            return
        keys = [self._text(member) for member in members]
        raw_values = await self._client.mget(keys)
        now = _utc_now()
        stale = [
            key
            for key, raw in zip(keys, raw_values, strict=False)
            if raw is None or self._expired(raw, now)
        ]
        if stale:
            pipeline = self._client.pipeline(transaction=True)
            for key in stale:
                pipeline.delete(key)
                pipeline.zrem(self._index_key, key)
            await pipeline.execute()

    async def _evict_if_full(self) -> None:
        max_entries = self.policy.max_entries
        if max_entries is None:
            return
        await self._purge_stale()
        while await self._client.zcard(self._index_key) >= max_entries:
            popped = await self._client.zpopmin(self._index_key, count=1)
            if not popped:
                return
            key = self._text(popped[0][0])
            await self._client.delete(key)

    async def store(self, entry: MemoryEntry) -> None:
        key = self._key(entry.key, entry.scope)
        existing = await self._client.get(key)
        if existing is None:
            await self._evict_if_full()
        now = _utc_now()
        expires_at = self._expiry(now)
        ttl = self._remaining_ttl(expires_at, now)
        pipeline = self._client.pipeline(transaction=True)
        if ttl == 0:
            pipeline.delete(key)
            pipeline.zrem(self._index_key, key)
        else:
            payload = self._record(entry, now, expires_at)
            if ttl is None:
                pipeline.set(key, payload)
            else:
                pipeline.set(key, payload, ex=ttl)
            pipeline.zadd(self._index_key, {key: now.timestamp()})
        await pipeline.execute()

    async def search(
        self, query: str, scope: str | None = None, limit: int = 10
    ) -> list[MemoryEntry]:
        if limit <= 0:
            return []
        entries = await self.list_entries(scope=scope)
        query_lower = query.lower()
        return [entry for entry in entries if query_lower in str(entry.value).lower()][:limit]

    async def get(self, key: str, scope: str | None = None) -> MemoryEntry | None:
        redis_key = self._key(key, scope)
        raw = await self._client.get(redis_key)
        if raw is None:
            await self._client.zrem(self._index_key, redis_key)
            return None
        if self._expired(raw, _utc_now()):
            await self._remove_key(redis_key)
            return None
        try:
            return self._decode(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self._remove_key(redis_key)
            return None

    async def delete(self, key: str, scope: str | None = None) -> bool:
        redis_key = self._key(key, scope)
        existed = await self._client.get(redis_key) is not None
        await self._remove_key(redis_key)
        return existed

    async def list_entries(self, scope: str | None = None) -> list[MemoryEntry]:
        members = await self._client.zrange(self._index_key, 0, -1)
        if not members:
            return []
        keys = [self._text(member) for member in members]
        raw_values = await self._client.mget(keys)
        now = _utc_now()
        entries: list[MemoryEntry] = []
        stale: list[str] = []
        for key, raw in zip(keys, raw_values, strict=False):
            if raw is None or self._expired(raw, now):
                stale.append(key)
                continue
            try:
                entry = self._decode(raw)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                stale.append(key)
                continue
            if scope is None or entry.scope == scope:
                entries.append(entry)
        if stale:
            pipeline = self._client.pipeline(transaction=True)
            for key in stale:
                pipeline.delete(key)
                pipeline.zrem(self._index_key, key)
            await pipeline.execute()
        return entries

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
