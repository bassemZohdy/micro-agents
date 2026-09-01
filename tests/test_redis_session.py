"""Redis session integration tests against the CI service container."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

redis = pytest.importorskip("redis.asyncio")

from micro_agent.session import RedisSessionProvider  # noqa: E402

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
    finally:
        await asyncio.gather(*(provider_a.delete(session_id) for session_id in session_ids))
        await provider_a.delete("expires-now")
        await client_a.aclose()
        await client_b.aclose()
