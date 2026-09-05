"""Approval/confirmation continuation state.

When a policy requires approval for a side-effect operation, the runtime
pauses the invocation instead of permanently denying it: the pending tool
requests and the conversation state are stored under a continuation id, and
the caller resumes (approve) or cancels (deny) the invocation with that id.
The store is an SPI — the built-in in-memory store keeps the default
deployment dependency-free, while the optional Redis store provides durable
cross-replica continuations.
"""

from __future__ import annotations

import inspect
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any
from urllib.parse import quote

from micro_agent.session.redis import _import_redis, _validate_endpoint

_DEFAULT_APPROVAL_TTL_SECONDS = 300.0


@dataclass
class PendingApproval:
    """A paused invocation waiting for an approval decision."""

    continuation_id: str
    agent_id: str
    tool_requests: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    all_tool_results: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    request_id: str | None = None
    session_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    expires_at: float | None = None

    def with_ttl(self, ttl_seconds: float) -> PendingApproval:
        return replace(self, expires_at=monotonic() + ttl_seconds)

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or monotonic()) >= self.expires_at


class ApprovalStore(ABC):
    """Stores pending approvals between the pause and the decision."""

    @abstractmethod
    async def save(self, approval: PendingApproval) -> None:
        """Store a pending approval."""

    @abstractmethod
    async def get(self, continuation_id: str) -> PendingApproval | None:
        """Return a non-expired pending approval, or ``None``."""

    @abstractmethod
    async def delete(self, continuation_id: str) -> None:
        """Consume a pending approval."""

    async def health_check(self) -> bool:
        """Return whether the approval store is available.

        Process-local stores are always available; durable implementations
        override this with a backend connectivity probe.
        """
        return True


class InMemoryApprovalStore(ApprovalStore):
    """Process-local pending-approval store with TTL expiry."""

    def __init__(self, default_ttl_seconds: float = _DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl_seconds
        self._approvals: dict[str, PendingApproval] = {}

    async def save(self, approval: PendingApproval) -> None:
        if approval.expires_at is None:
            approval = approval.with_ttl(self._default_ttl)
        self._approvals[approval.continuation_id] = approval

    async def get(self, continuation_id: str) -> PendingApproval | None:
        approval = self._approvals.get(continuation_id)
        if approval is None:
            return None
        if approval.expired():
            del self._approvals[continuation_id]
            return None
        return approval

    async def delete(self, continuation_id: str) -> None:
        self._approvals.pop(continuation_id, None)


class RedisApprovalStore(ApprovalStore):
    """Durable approval continuation store backed by Redis.

    Expiry is persisted as a wall-clock timestamp and reconstructed into the
    process-local monotonic clock on read. Redis key TTLs provide background
    eviction, while the timestamp protects correctness across process restarts
    and clock differences between Redis and the worker.
    """

    def __init__(
        self,
        endpoint: str = "redis://localhost:6379/0",
        *,
        default_ttl_seconds: float = _DEFAULT_APPROVAL_TTL_SECONDS,
        namespace: str = "micro-agent",
        client: Any | None = None,
        connect_timeout_seconds: float = 5.0,
    ) -> None:
        _validate_endpoint(endpoint)
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be positive")
        if not namespace or namespace.strip() != namespace:
            raise ValueError("namespace must be a non-empty value without surrounding whitespace")

        self._endpoint = endpoint
        self._default_ttl = default_ttl_seconds
        self._prefix = f"{namespace}:approval:"
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

    def _key(self, continuation_id: str) -> str:
        return f"{self._prefix}{quote(continuation_id, safe='')}"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)

    @staticmethod
    def _encode(approval: PendingApproval, expires_at_epoch: float) -> str:
        return json.dumps(
            {
                "continuation_id": approval.continuation_id,
                "agent_id": approval.agent_id,
                "tool_requests": approval.tool_requests,
                "messages": approval.messages,
                "all_tool_results": approval.all_tool_results,
                "iterations": approval.iterations,
                "request_id": approval.request_id,
                "session_id": approval.session_id,
                "input_payload": approval.input_payload,
                "expires_at_epoch": expires_at_epoch,
            },
            default=str,
        )

    @classmethod
    def _decode(cls, raw: Any) -> PendingApproval | None:
        data = json.loads(cls._text(raw))
        if not isinstance(data, dict) or not data.get("continuation_id"):
            raise ValueError("Redis approval payload is not an approval record")
        expires_at_epoch = float(data["expires_at_epoch"])
        remaining = expires_at_epoch - time.time()
        if remaining <= 0:
            return None
        return PendingApproval(
            continuation_id=str(data["continuation_id"]),
            agent_id=str(data.get("agent_id", "")),
            tool_requests=list(data.get("tool_requests") or []),
            messages=list(data.get("messages") or []),
            all_tool_results=list(data.get("all_tool_results") or []),
            iterations=int(data.get("iterations", 0)),
            request_id=(str(data["request_id"]) if data.get("request_id") is not None else None),
            session_id=(str(data["session_id"]) if data.get("session_id") is not None else None),
            input_payload=dict(data.get("input_payload") or {}),
            expires_at=monotonic() + remaining,
        )

    async def save(self, approval: PendingApproval) -> None:
        expires_at = approval.expires_at
        if expires_at is None:
            approval = approval.with_ttl(self._default_ttl)
            expires_at = approval.expires_at
        assert expires_at is not None
        remaining = max(0.0, expires_at - monotonic())
        ttl = max(1, math.ceil(remaining))
        payload = self._encode(approval, time.time() + remaining)
        await self._client.set(self._key(approval.continuation_id), payload, ex=ttl)

    async def get(self, continuation_id: str) -> PendingApproval | None:
        key = self._key(continuation_id)
        raw = await self._client.get(key)
        if raw is None:
            return None
        try:
            approval = self._decode(raw)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            await self._client.delete(key)
            return None
        if approval is None:
            await self._client.delete(key)
        return approval

    async def delete(self, continuation_id: str) -> None:
        await self._client.delete(self._key(continuation_id))

    async def health_check(self) -> bool:
        """Return whether Redis answers a ping probe."""
        return bool(await self._client.ping())

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


__all__ = [
    "ApprovalStore",
    "InMemoryApprovalStore",
    "PendingApproval",
    "RedisApprovalStore",
]
