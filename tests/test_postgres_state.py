"""PostgreSQL state-provider integration tests.

Run against a real PostgreSQL server (the ``MICRO_AGENT_TEST_PG_DSN``
environment variable, e.g. ``postgres://postgres:secret@localhost:55432/postgres``).
Proves the production-state-provider acceptance: two independent provider
instances — as separate processes would be — share session, memory, and
idempotency state, and concurrent writers cannot lose updates.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from micro_agent.memory.memory import MemoryEntry, MemoryPolicy
from micro_agent.memory.postgres import PostgresIdempotencyStore, PostgresMemoryProvider
from micro_agent.security.side_effects import Operation, OperationResult
from micro_agent.session.postgres import PostgresSessionProvider
from micro_agent.state import StateConflictError

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

pytest.importorskip("asyncpg")

DSN = os.environ.get("MICRO_AGENT_TEST_PG_DSN", "")


def _dsn() -> str:
    if not DSN:
        pytest.skip("MICRO_AGENT_TEST_PG_DSN is not set; PostgreSQL tests skipped")
    return DSN


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Each test starts from empty state tables."""
    import asyncpg

    conn = await asyncpg.connect(_dsn())
    try:
        for table in ("micro_agent_sessions", "micro_agent_memory", "micro_agent_idempotency"):
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
    finally:
        await conn.close()
    yield


@pytest.mark.asyncio
async def test_session_roundtrip_expiry_and_delete():
    provider = PostgresSessionProvider(_dsn(), ttl_seconds=60)
    try:
        session_id = f"s-{uuid4().hex[:8]}"
        session = await provider.create(session_id, ttl_seconds=60)
        session.messages.append({"role": "user", "content": "hi"})
        await provider.update(session)

        fetched = await provider.get(session_id)
        assert fetched is not None
        assert fetched.messages == [{"role": "user", "content": "hi"}]
        assert fetched.version == 2

        active = await provider.list_active()
        assert [m.session_id for m in active] == [session_id]

        await provider.delete(session_id)
        assert await provider.get(session_id) is None
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_two_providers_share_state_and_conflicts_are_detected():
    """The concurrency core: two independent writers, no lost updates."""
    first = PostgresSessionProvider(_dsn())
    second = PostgresSessionProvider(_dsn())
    try:
        session_id = f"shared-{uuid4().hex[:8]}"
        session = await first.create(session_id)
        session.messages.append({"role": "user", "content": "from-first"})
        await first.update(session)

        other = await second.get(session_id)
        assert other is not None
        assert other.messages == [{"role": "user", "content": "from-first"}]

        # The second writer advances the version; the first writer's
        # still-held version is now stale and must lose the race.
        other.messages.append({"role": "assistant", "content": "fresh"})
        await second.update(other)

        session.messages.append({"role": "assistant", "content": "stale"})
        with pytest.raises(StateConflictError, match="version conflict"):
            await first.update(session)

        reread = await first.get(session_id)
        assert reread is not None
        assert reread.messages[-1] == {"role": "assistant", "content": "fresh"}
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_concurrent_writers_serialize_without_lost_updates():
    """Concurrent same-provider writers all land or conflict, never vanish."""
    provider = PostgresSessionProvider(_dsn())
    try:
        session_id = f"concurrent-{uuid4().hex[:8]}"
        await provider.create(session_id)

        async def writer(index: int) -> str:
            s = await provider.get(session_id)
            assert s is not None
            s.messages.append({"role": "user", "content": f"w{index}"})
            try:
                await provider.update(s)
                return "ok"
            except StateConflictError:
                return "conflict"

        results = await asyncio.gather(*(writer(i) for i in range(8)))
        final = await provider.get(session_id)
        assert final is not None
        applied = len(results) - results.count("conflict")
        assert len(final.messages) == applied
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_session_tenants_are_isolated():
    provider = PostgresSessionProvider(_dsn())
    try:
        session_id = f"tenant-{uuid4().hex[:8]}"
        await provider.create(session_id, tenant_id="acme")
        # Same session id in the unscoped namespace is a different session.
        unscoped = await provider.create(session_id)
        assert unscoped.tenant_id is None

        scoped = await provider.get(session_id, tenant_id="acme")
        assert scoped is not None
        assert scoped.tenant_id == "acme"
        assert await provider.get(session_id, tenant_id="other") is None

        await provider.delete(session_id, tenant_id="acme")
        assert await provider.get(session_id, tenant_id="acme") is None
        assert await provider.get(session_id) is not None
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_idempotency_key_shared_across_stores_under_concurrency():
    """Two stores race one key: exactly one executes, the rest deduplicate."""
    first = PostgresIdempotencyStore(_dsn(), ttl_seconds=300)
    second = PostgresIdempotencyStore(_dsn(), ttl_seconds=300)
    try:
        operation = Operation(name="submit", arguments={}, idempotency_key=f"key-{uuid4().hex[:8]}")
        results = await asyncio.gather(first.claim(operation), second.claim(operation))
        claimed = [results[0][0], results[1][0]]
        assert sum(claimed) == 1, f"exactly one worker must reserve, saw {claimed}"
        winner, loser = (first, second) if claimed[0] else (second, first)
        # The loser observes an in-progress reservation, not a completed result.
        assert results[1 if claimed[0] else 0][1].status == "in_progress"

        await winner.record(
            operation,
            OperationResult(
                operation_id=operation.operation_id, status="success", output={"done": True}
            ),
        )

        claimed_again, prior = await loser.claim(operation)
        assert claimed_again is False
        assert prior is not None
        assert prior.status == "success"
        assert prior.output == {"done": True}
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_idempotency_record_from_non_owner_is_ignored():
    store = PostgresIdempotencyStore(_dsn(), ttl_seconds=300)
    try:
        operation = Operation(name="submit", arguments={}, idempotency_key=f"key-{uuid4().hex[:8]}")
        claimed, _prior = await store.claim(operation)
        assert claimed is True
        # A late worker whose reservation was reclaimed must not overwrite a
        # newer attempt's result.
        impostor = Operation(name="submit", arguments={}, idempotency_key=operation.idempotency_key)
        await store.record(
            impostor,
            OperationResult(operation_id=impostor.operation_id, output={"impostor": True}),
        )
        _claimed, prior = await store.claim(operation)
        assert prior is not None
        assert prior.status == "in_progress"
    finally:
        await store.aclose()


@pytest.mark.asyncio
async def test_memory_provider_scopes_search_and_expires():
    provider = PostgresMemoryProvider(_dsn(), policy=MemoryPolicy(ttl_seconds=300))
    try:
        await provider.store(
            MemoryEntry(key="pref", value={"text": "prefers email"}, scope="tenant:acme")
        )
        await provider.store(
            MemoryEntry(key="pref", value={"text": "prefers phone"}, scope="tenant:other")
        )
        tenant_entries = await provider.list_entries("tenant:acme")
        assert len(tenant_entries) == 1
        assert tenant_entries[0].value == {"text": "prefers email"}

        hits = await provider.search("email", scope="tenant:acme")
        assert len(hits) == 1
        phone_hits = await provider.search("phone", scope="tenant:other")
        assert len(phone_hits) == 1 and phone_hits[0].value == {"text": "prefers phone"}
        # Search is scope-isolated: the acme entry never leaks.
        assert await provider.search("email", scope="tenant:other") == []

        assert await provider.delete("pref", scope="tenant:acme") is True
        assert await provider.get("pref", scope="tenant:acme") is None
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_memory_provider_tenants_are_isolated_and_versions_conflict():
    provider = PostgresMemoryProvider(_dsn())
    try:
        await provider.store(MemoryEntry(key="k", value="acme", tenant_id="acme"))
        await provider.store(MemoryEntry(key="k", value="unscoped"))

        acme = await provider.get("k", tenant_id="acme")
        assert acme is not None and acme.value == "acme" and acme.version == 1
        assert await provider.get("k", tenant_id="other") is None

        # Stale writers lose the optimistic-concurrency race.
        stale = await provider.get("k", tenant_id="acme")
        assert stale is not None
        fresh = await provider.get("k", tenant_id="acme")
        assert fresh is not None
        fresh.value = "updated"
        await provider.store(fresh)
        stale.value = "stale"
        with pytest.raises(StateConflictError, match="version conflict"):
            await provider.store(stale)

        # Unscoped and tenant namespaces never collide.
        unscoped = await provider.get("k")
        assert unscoped is not None and unscoped.value == "unscoped"
    finally:
        await provider.aclose()
