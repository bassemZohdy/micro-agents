"""Shared gateway resilience state.

The gateway keeps a process-local implementation by default. This module
provides the optional Redis implementation used when independently scaled
gateway workers must agree on rate limits, circuit state, and bulkhead leases.
All mutations are atomic Redis scripts and all keys have bounded lifetimes.
"""

from __future__ import annotations

import inspect
from typing import Any, Protocol
from urllib.parse import quote
from uuid import uuid4

from micro_agent.session.redis import _import_redis, _validate_endpoint

_RATE_LIMIT_SCRIPT = """
local rate = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
if rate == nil or rate <= 0 then
  return 0
end
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + tonumber(server_time[2]) / 1000000
local tokens = tonumber(redis.call('HGET', KEYS[1], 'tokens') or rate)
local updated = tonumber(redis.call('HGET', KEYS[1], 'updated') or now)
tokens = math.min(rate, tokens + math.max(0, now - updated) * rate / 60)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', KEYS[1], ttl)
return allowed
"""

_CIRCUIT_AVAILABLE_SCRIPT = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + tonumber(server_time[2]) / 1000000
local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or 0)
local probe = redis.call('HGET', KEYS[1], 'probe') or ''
if open_until > now then
  return 0
end
if open_until > 0 and probe ~= '' then
  return 0
end
if open_until > 0 then
  redis.call('HSET', KEYS[1], 'probe', ARGV[1])
end
redis.call('EXPIRE', KEYS[1], ARGV[2])
return 1
"""

_CIRCUIT_FAILURE_SCRIPT = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + tonumber(server_time[2]) / 1000000
local failures = tonumber(redis.call('HGET', KEYS[1], 'failures') or 0) + 1
local threshold = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or 0)
if failures >= threshold then
  open_until = now + cooldown
  redis.call('HSET', KEYS[1], 'failures', failures, 'open_until', open_until, 'probe', '')
else
  redis.call('HSET', KEYS[1], 'failures', failures)
end
redis.call('EXPIRE', KEYS[1], ttl)
return failures
"""

_BULKHEAD_ACQUIRE_SCRIPT = """
local server_time = redis.call('TIME')
local now = tonumber(server_time[1]) + tonumber(server_time[2]) / 1000000
local limit = tonumber(ARGV[1])
local lease = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= limit then
  return 0
end
redis.call('ZADD', KEYS[1], now + lease, ARGV[3])
redis.call('EXPIRE', KEYS[1], math.ceil(lease + 1))
return 1
"""


class GatewayStateStore(Protocol):
    """Shared resilience operations consumed by :class:`cloud.Gateway`."""

    async def allow_rate_limit(self, key: str, rate_per_minute: int) -> bool:
        """Atomically consume one token for a caller bucket."""

    async def circuit_available(
        self, key: str, failure_threshold: int, cooldown_seconds: float
    ) -> bool:
        """Return whether this target may receive a request."""

    async def record_success(self, key: str) -> None:
        """Reset shared circuit state after a successful response."""

    async def record_failure(
        self, key: str, failure_threshold: int, cooldown_seconds: float
    ) -> None:
        """Record one shared transport or upstream failure."""

    async def try_acquire_bulkhead(self, key: str, max_concurrency: int) -> str | None:
        """Return a lease token, or ``None`` when the target is saturated."""

    async def release_bulkhead(self, key: str, lease: str) -> None:
        """Release a previously acquired target lease."""

    async def health_check(self) -> bool:
        """Return whether the backing store is reachable."""


class RedisGatewayStateStore:
    """Redis implementation of shared gateway resilience state.

    Rate limits and circuit transitions use atomic Lua scripts. Bulkheads use
    expiring sorted-set leases, so a worker crash cannot permanently consume a
    slot. Inject a Redis client for tests or a deployment-owned connection;
    the store only closes a client that it created itself.
    """

    def __init__(
        self,
        endpoint: str = "redis://localhost:6379/0",
        *,
        namespace: str = "micro-agent",
        rate_limit_ttl_seconds: int = 120,
        bulkhead_lease_seconds: float = 60.0,
        client: Any | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        _validate_endpoint(endpoint)
        if not namespace or namespace.strip() != namespace:
            raise ValueError("namespace must be a non-empty value without surrounding whitespace")
        if rate_limit_ttl_seconds < 1:
            raise ValueError("rate_limit_ttl_seconds must be positive")
        if bulkhead_lease_seconds <= 0:
            raise ValueError("bulkhead_lease_seconds must be positive")
        self._endpoint = endpoint
        self._prefix = f"{namespace}:gateway:"
        self._rate_limit_ttl = rate_limit_ttl_seconds
        self._bulkhead_lease = float(bulkhead_lease_seconds)
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

    def _key(self, kind: str, value: str) -> str:
        return f"{self._prefix}{kind}:{quote(value, safe='')}"

    async def allow_rate_limit(self, key: str, rate_per_minute: int) -> bool:
        result = await self._client.eval(
            _RATE_LIMIT_SCRIPT,
            1,
            self._key("rate", key),
            str(rate_per_minute),
            str(self._rate_limit_ttl),
        )
        return bool(int(result))

    async def circuit_available(
        self, key: str, failure_threshold: int, cooldown_seconds: float
    ) -> bool:
        del failure_threshold
        ttl = max(60, int(cooldown_seconds * 2) + 1)
        result = await self._client.eval(
            _CIRCUIT_AVAILABLE_SCRIPT,
            1,
            self._key("circuit", key),
            uuid4().hex,
            str(ttl),
        )
        return bool(int(result))

    async def record_success(self, key: str) -> None:
        await self._client.delete(self._key("circuit", key))

    async def record_failure(
        self, key: str, failure_threshold: int, cooldown_seconds: float
    ) -> None:
        ttl = max(60, int(cooldown_seconds * 2) + 1)
        await self._client.eval(
            _CIRCUIT_FAILURE_SCRIPT,
            1,
            self._key("circuit", key),
            str(failure_threshold),
            str(cooldown_seconds),
            str(ttl),
        )

    async def try_acquire_bulkhead(self, key: str, max_concurrency: int) -> str | None:
        lease = uuid4().hex
        result = await self._client.eval(
            _BULKHEAD_ACQUIRE_SCRIPT,
            1,
            self._key("bulkhead", key),
            str(max_concurrency),
            str(self._bulkhead_lease),
            lease,
        )
        return lease if int(result) else None

    async def release_bulkhead(self, key: str, lease: str) -> None:
        await self._client.zrem(self._key("bulkhead", key), lease)

    async def health_check(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

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


__all__ = ["GatewayStateStore", "RedisGatewayStateStore"]
