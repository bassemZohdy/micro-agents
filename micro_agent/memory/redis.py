"""Redis-backed memory provider for shared deployments."""

from __future__ import annotations

import inspect
import json
import math
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from micro_agent.memory.memory import MemoryEntry, MemoryPolicy, MemoryProvider
from micro_agent.session.redis import _import_redis, _validate_endpoint
from micro_agent.session.session import _iso, _utc_now
from micro_agent.state import StateConflictError


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

    def _key(self, key: str, scope: str | None, tenant_id: str | None = None) -> str:
        tenant_prefix = "" if tenant_id is None else f"tenant:{quote(tenant_id, safe='')}:"
        return f"{self._prefix}{tenant_prefix}{self._scope(scope)}:{key}"

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
        tenant_id = data.get("tenant_id")
        version = int(data.get("version") or 1)
        if stored_at is not None:
            metadata.setdefault("stored_at", stored_at)
        if expires_at is not None:
            metadata.setdefault("expires_at", expires_at)
        return MemoryEntry(
            key=str(data["key"]),
            value=data.get("value"),
            scope=str(data["scope"]),
            metadata=metadata,
            tenant_id=str(tenant_id) if tenant_id is not None else None,
            version=version,
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
                "tenant_id": entry.tenant_id,
                "version": entry.version or 1,
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

    async def store(self, entry: MemoryEntry, *, expected_version: int | None = None) -> None:
        key = self._key(entry.key, entry.scope, entry.tenant_id)
        expected = expected_version if expected_version is not None else entry.version

        def prepare(existing: Any) -> tuple[datetime, str | None, int]:
            if existing is None:
                actual_version = 0
            else:
                try:
                    actual_version = self._decode(existing).version or 1
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    actual_version = 0
            if actual_version == 0:
                if expected not in (0, None):
                    raise StateConflictError("memory", entry.key, expected, 0)
                new_version = 1
            else:
                if expected and expected != actual_version:
                    raise StateConflictError("memory", entry.key, expected, actual_version)
                new_version = actual_version + 1
            now = _utc_now()
            return now, self._expiry(now), new_version

        for _attempt in range(3):
            pipeline = self._client.pipeline(transaction=True)
            watch = getattr(pipeline, "watch", None)
            if not callable(watch):
                existing = await self._client.get(key)
                if existing is None:
                    await self._evict_if_full()
                now, expires_at, new_version = prepare(existing)
                entry.version = new_version
                await self._write_entry(key, entry, now, expires_at)
                return
            try:
                watched = watch(key)
                if inspect.isawaitable(watched):
                    await watched
                existing = pipeline.get(key)
                if inspect.isawaitable(existing):
                    existing = await existing
                if existing is None:
                    await self._evict_if_full()
                now, expires_at, new_version = prepare(existing)
                entry.version = new_version
                ttl = self._remaining_ttl(expires_at, now)
                pipeline.multi()
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
        raise StateConflictError("memory", entry.key, expected or 0, -1)

    async def _write_entry(
        self, key: str, entry: MemoryEntry, now: datetime, expires_at: str | None
    ) -> None:
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
        self,
        query: str,
        scope: str | None = None,
        limit: int = 10,
        *,
        tenant_id: str | None = None,
    ) -> list[MemoryEntry]:
        if limit <= 0:
            return []
        entries = await self.list_entries(scope=scope, tenant_id=tenant_id)
        query_lower = query.lower()
        return [entry for entry in entries if query_lower in str(entry.value).lower()][:limit]

    async def get(
        self, key: str, scope: str | None = None, *, tenant_id: str | None = None
    ) -> MemoryEntry | None:
        redis_key = self._key(key, scope, tenant_id)
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

    async def delete(
        self, key: str, scope: str | None = None, *, tenant_id: str | None = None
    ) -> bool:
        redis_key = self._key(key, scope, tenant_id)
        existed = await self._client.get(redis_key) is not None
        await self._remove_key(redis_key)
        return existed

    async def list_entries(
        self, scope: str | None = None, *, tenant_id: str | None = None
    ) -> list[MemoryEntry]:
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
            if entry.tenant_id == tenant_id and (scope is None or entry.scope == scope):
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
