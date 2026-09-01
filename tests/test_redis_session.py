"""Redis session integration tests against the CI service container."""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis.asyncio")

from micro_agent.memory import MemoryEntry, RedisMemoryProvider  # noqa: E402
from micro_agent.security import Operation, OperationResult, RedisOperationRegistry  # noqa: E402
from micro_agent.session import RedisSessionProvider  # noqa: E402
from micro_agent.state import StateConflictError  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_provider_shares_and_expires_sessions() -> None:
    endpoint = os.getenv("MICRO_AGENT_REDIS_URL", "redis://localhost:6379/0")
    namespace = f"micro-agent-test-{uuid4().hex}"
    client_a = redis.from_url(endpoint, decode_responses=True)
    client_b = redis.from_url(endpoint, decode_responses=True)
    provider_a = RedisSessionProvider(
        endpoint, namespace=namespace, client=client_a, ttl_seconds=30
    )
    provider_b = RedisSessionProvider(
        endpoint, namespace=namespace, client=client_b, ttl_seconds=30
    )
    session_ids = [f"session-{index}" for index in range(10)]
    try:
        await asyncio.gather(*(provider_a.create(session_id) for session_id in session_ids))
        shared = await provider_b.get(session_ids[0])
        assert shared is not None
        shared.messages.append({"role": "user", "content": "shared"})
        await provider_b.update(shared)
        seen = await provider_a.get(session_ids[0])
        assert seen is not None
        assert seen.messages[-1]["content"] == "shared"
        assert len(await provider_a.list_active()) == len(session_ids)

        await provider_a.create("expires-now", ttl_seconds=0)
        assert await provider_b.get("expires-now") is None

        await provider_a.create("tenant-shared", tenant_id="tenant-a")
        await provider_a.create("tenant-shared", tenant_id="tenant-b")
        assert await provider_b.get("tenant-shared") is None
        stale = deepcopy(await provider_b.get("tenant-shared", tenant_id="tenant-a"))
        current = await provider_a.get("tenant-shared", tenant_id="tenant-a")
        await provider_a.update(current)
        with pytest.raises(StateConflictError):
            await provider_b.update(stale)
    finally:
        await asyncio.gather(*(provider_a.delete(session_id) for session_id in session_ids))
        await provider_a.delete("expires-now")
        await provider_a.delete("tenant-shared", tenant_id="tenant-a")
        await provider_a.delete("tenant-shared", tenant_id="tenant-b")
        await client_a.aclose()
        await client_b.aclose()


@pytest.mark.asyncio
async def test_redis_memory_provider_shares_entries() -> None:
    endpoint = os.getenv("MICRO_AGENT_REDIS_URL", "redis://localhost:6379/0")
    namespace = f"micro-agent-memory-test-{uuid4().hex}"
    client_a = redis.from_url(endpoint, decode_responses=True)
    client_b = redis.from_url(endpoint, decode_responses=True)
    provider_a = RedisMemoryProvider(endpoint, namespace=namespace, client=client_a)
    provider_b = RedisMemoryProvider(endpoint, namespace=namespace, client=client_b)
    try:
        await provider_a.store(MemoryEntry(key="preference", value="dark mode", scope="user"))
        shared = await provider_b.get("preference", scope="user")
        assert shared is not None
        assert shared.value == "dark mode"
        assert len(await provider_b.search("dark", scope="user")) == 1
        await provider_a.store(
            MemoryEntry(key="preference", value="tenant-a", scope="user", tenant_id="tenant-a")
        )
        await provider_a.store(
            MemoryEntry(key="preference", value="tenant-b", scope="user", tenant_id="tenant-b")
        )
        stale = deepcopy(await provider_b.get("preference", scope="user", tenant_id="tenant-a"))
        current = await provider_a.get("preference", scope="user", tenant_id="tenant-a")
        current.value = "updated"
        await provider_a.store(current)
        with pytest.raises(StateConflictError):
            await provider_b.store(stale)
        assert await provider_a.health_check() is True
    finally:
        await provider_a.delete("preference", scope="user")
        await provider_a.delete("preference", scope="user", tenant_id="tenant-a")
        await provider_a.delete("preference", scope="user", tenant_id="tenant-b")
        await client_a.aclose()
        await client_b.aclose()


@pytest.mark.asyncio
async def test_redis_operation_registry_claims_once_and_shares_result() -> None:
    endpoint = os.getenv("MICRO_AGENT_REDIS_URL", "redis://localhost:6379/0")
    namespace = f"micro-agent-operation-test-{uuid4().hex}"
    client_a = redis.from_url(endpoint, decode_responses=True)
    client_b = redis.from_url(endpoint, decode_responses=True)
    registry_a = RedisOperationRegistry(endpoint, namespace=namespace, client=client_a)
    registry_b = RedisOperationRegistry(endpoint, namespace=namespace, client=client_b)
    operation_a = Operation(idempotency_key="payment-1", name="payment")
    operation_b = Operation(idempotency_key="payment-1", name="payment")
    try:
        assert await registry_a.claim(operation_a) == (True, None)
        claimed, pending = await registry_b.claim(operation_b)
        assert claimed is False
        assert pending is not None
        assert pending.status == "in_progress"
        await registry_a.record(
            operation_a,
            OperationResult(operation_id=operation_a.operation_id, output={"receipt": "r-1"}),
        )
        prior = await registry_b.find_by_idempotency_key("payment-1")
        assert prior is not None
        assert prior.output == {"receipt": "r-1"}
    finally:
        await client_a.delete(f"{namespace}:operation:payment-1")
        await client_a.aclose()
        await client_b.aclose()
