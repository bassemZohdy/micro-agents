"""Redis-backed operation registry for distributed idempotency."""

from __future__ import annotations

import inspect
import json
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from micro_agent.security.side_effects import (
    Operation,
    OperationRegistryProtocol,
    OperationResult,
)
from micro_agent.session.redis import _import_redis, _validate_endpoint


class RedisOperationRegistry(OperationRegistryProtocol):
    """Atomically claim and persist idempotent operation results in Redis.

    A ``SET NX`` reservation prevents two independently scaled runtimes from
    executing the same idempotency key concurrently. Reservations and results
    both expire after ``ttl_seconds`` so an abandoned operation cannot block a
    key forever. The caller should retain the same operation key for retries.
    """

    def __init__(
        self,
        endpoint: str = "redis://localhost:6379/0",
        *,
        ttl_seconds: int = 86_400,
        namespace: str = "micro-agent",
        client: Any | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        _validate_endpoint(endpoint)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
            raise ValueError("ttl_seconds must be a positive integer")
        if not namespace or namespace.strip() != namespace:
            raise ValueError("namespace must be a non-empty value without surrounding whitespace")

        self._endpoint = endpoint
        self._ttl_seconds = ttl_seconds
        self._namespace = namespace
        self._prefix = f"{namespace}:operation:"
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

    def _key(self, idempotency_key: str, tenant_id: str | None = None) -> str:
        if tenant_id is None:
            return f"{self._prefix}{idempotency_key}"
        return f"{self._prefix}tenant:{quote(tenant_id, safe='')}:{idempotency_key}"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    @staticmethod
    def _encode(result: OperationResult) -> str:
        return json.dumps(
            {
                "operation_id": result.operation_id,
                "status": result.status,
                "output": result.output,
                "error": result.error,
                "was_deduplicated": result.was_deduplicated,
            },
            default=str,
        )

    @classmethod
    def _decode(cls, raw: Any) -> OperationResult:
        data = json.loads(cls._text(raw))
        if not isinstance(data, dict) or "operation_id" not in data or "status" not in data:
            raise ValueError("Redis operation payload is not an operation result")
        return OperationResult(
            operation_id=str(data["operation_id"]),
            status=str(data["status"]),
            output=data.get("output"),
            error=data.get("error"),
            was_deduplicated=bool(data.get("was_deduplicated", False)),
        )

    async def _get_result(
        self, idempotency_key: str, tenant_id: str | None = None
    ) -> OperationResult | None:
        redis_key = self._key(idempotency_key, tenant_id)
        raw = await self._client.get(redis_key)
        if raw is None:
            return None
        try:
            return self._decode(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self._client.delete(redis_key)
            return None

    async def claim(self, operation: Operation) -> tuple[bool, OperationResult | None]:
        """Reserve an idempotency key with one atomic Redis write."""
        key = operation.idempotency_key
        if not key:
            return True, None

        redis_key = self._key(key, operation.tenant_id)
        reservation = OperationResult(operation_id=operation.operation_id, status="in_progress")
        claimed = await self._client.set(
            redis_key,
            self._encode(reservation),
            nx=True,
            ex=self._ttl_seconds,
        )
        if claimed:
            return True, None

        prior = await self._get_result(key, operation.tenant_id)
        if prior is None:
            # A key can expire between SET NX and GET. Retry once so a caller
            # does not receive a false duplicate after the reservation TTL.
            claimed = await self._client.set(
                redis_key,
                self._encode(reservation),
                nx=True,
                ex=self._ttl_seconds,
            )
            if claimed:
                return True, None
            prior = await self._get_result(key, operation.tenant_id)
        if prior is None:
            prior = OperationResult(status="in_progress")
        return False, replace(prior, was_deduplicated=True)

    async def is_duplicate(self, operation: Operation) -> bool:
        """Return whether the key is currently reserved or completed."""
        if not operation.idempotency_key:
            return False
        return await self._get_result(operation.idempotency_key, operation.tenant_id) is not None

    async def find_by_idempotency_key(
        self, key: str, tenant_id: str | None = None
    ) -> OperationResult | None:
        """Return a reservation or completed result for ``key``."""
        return await self._get_result(key, tenant_id)

    async def record(self, operation: Operation, result: OperationResult) -> None:
        """Complete a reservation while retaining the idempotency TTL.

        Only the operation that owns the reservation may publish a result. A
        result arriving after the reservation expired or was reclaimed is
        ignored, preventing a late worker from overwriting a newer attempt.
        """
        key = operation.idempotency_key
        if not key:
            return
        redis_key = self._key(key, operation.tenant_id)
        current = await self._get_result(key, operation.tenant_id)
        if current is None or current.operation_id != operation.operation_id:
            return
        await self._client.set(redis_key, self._encode(result), ex=self._ttl_seconds)

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
