"""Integration coverage for shared Cloud gateway resilience state."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis.asyncio")

from cloud.gateway_state import RedisGatewayStateStore  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_redis_gateway_state_coordinates_rate_circuit_and_bulkhead() -> None:
    endpoint = os.getenv("MICRO_AGENT_REDIS_URL", "redis://localhost:6379/0")
    namespace = f"micro-agent-gateway-test-{uuid4().hex}"
    client_a = redis.from_url(endpoint, decode_responses=True)
    client_b = redis.from_url(endpoint, decode_responses=True)
    state_a = RedisGatewayStateStore(endpoint, namespace=namespace, client=client_a)
    state_b = RedisGatewayStateStore(endpoint, namespace=namespace, client=client_b)
    try:
        assert await state_a.allow_rate_limit("route:tenant-a", 1)
        assert not await state_b.allow_rate_limit("route:tenant-a", 1)

        await state_a.record_failure("route:target", 1, 0.02)
        assert not await state_b.circuit_available("route:target", 1, 0.02)
        await asyncio.sleep(0.04)
        assert await state_b.circuit_available("route:target", 1, 0.02)
        assert not await state_a.circuit_available("route:target", 1, 0.02)
        await state_b.record_success("route:target")

        lease = await state_a.try_acquire_bulkhead("route:target", 1)
        assert lease is not None
        assert await state_b.try_acquire_bulkhead("route:target", 1) is None
        await state_a.release_bulkhead("route:target", lease)
        assert await state_b.try_acquire_bulkhead("route:target", 1) is not None
        assert await state_a.health_check()
    finally:
        keys = await client_a.keys(f"{namespace}:*")
        if keys:
            await client_a.delete(*keys)
        await client_a.aclose()
        await client_b.aclose()
