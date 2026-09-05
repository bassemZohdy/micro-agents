"""Unit tests for micro_agent.security.approvals."""

from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from micro_agent.security.approvals import (
    ApprovalStore,
    InMemoryApprovalStore,
    PendingApproval,
    RedisApprovalStore,
)
from tests.fake_redis import FakeRedis, FakeRedisBackend


def _make_approval(
    continuation_id: str = "cont-1",
    agent_id: str = "agent-1",
    **kwargs,
) -> PendingApproval:
    return PendingApproval(
        continuation_id=continuation_id,
        agent_id=agent_id,
        tool_requests=[{"tool": "test"}],
        messages=[{"role": "user", "content": "hello"}],
        **kwargs,
    )


class TestPendingApproval:
    def test_with_ttl_sets_expires_at(self):
        approval = _make_approval()
        before = monotonic()
        with_ttl = approval.with_ttl(60.0)
        after = monotonic()
        assert with_ttl.expires_at is not None
        assert before + 60.0 <= with_ttl.expires_at <= after + 60.0

    def test_expired_returns_false_when_no_expiry(self):
        approval = _make_approval()
        assert approval.expired() is False

    def test_expired_returns_false_before_expiry(self):
        approval = _make_approval(expires_at=monotonic() + 1000)
        assert approval.expired() is False

    def test_expired_returns_true_after_expiry(self):
        approval = _make_approval(expires_at=monotonic() - 1)
        assert approval.expired() is True

    def test_expired_uses_provided_now(self):
        approval = _make_approval(expires_at=100.0)
        assert approval.expired(now=99.0) is False
        assert approval.expired(now=100.0) is True
        assert approval.expired(now=101.0) is True

    def test_with_ttl_does_not_mutate_original(self):
        approval = _make_approval()
        _ = approval.with_ttl(60.0)
        assert approval.expires_at is None


class TestInMemoryApprovalStore:
    @pytest.fixture
    def store(self) -> InMemoryApprovalStore:
        return InMemoryApprovalStore(default_ttl_seconds=60.0)

    async def test_save_and_get(self, store: InMemoryApprovalStore):
        approval = _make_approval()
        await store.save(approval)
        retrieved = await store.get("cont-1")
        assert retrieved is not None
        assert retrieved.continuation_id == "cont-1"

    async def test_get_missing_returns_none(self, store: InMemoryApprovalStore):
        assert await store.get("nonexistent") is None

    async def test_save_applies_default_ttl(self, store: InMemoryApprovalStore):
        approval = _make_approval()
        await store.save(approval)
        retrieved = await store.get("cont-1")
        assert retrieved is not None
        assert retrieved.expires_at is not None

    async def test_save_preserves_existing_ttl(self, store: InMemoryApprovalStore):
        approval = _make_approval(expires_at=monotonic() + 999)
        await store.save(approval)
        retrieved = await store.get("cont-1")
        assert retrieved is not None
        assert retrieved.expires_at is not None
        assert retrieved.expires_at > monotonic() + 900

    async def test_get_expired_returns_none(self):
        store = InMemoryApprovalStore(default_ttl_seconds=0.0)
        approval = _make_approval()
        await store.save(approval)
        # Allow TTL to expire
        await asyncio.sleep(0.01)
        assert await store.get("cont-1") is None

    async def test_delete_removes_approval(self, store: InMemoryApprovalStore):
        approval = _make_approval()
        await store.save(approval)
        await store.delete("cont-1")
        assert await store.get("cont-1") is None

    async def test_delete_nonexistent_is_noop(self, store: InMemoryApprovalStore):
        await store.delete("nonexistent")
        assert await store.get("nonexistent") is None

    async def test_overwrite_same_continuation_id(self, store: InMemoryApprovalStore):
        approval1 = _make_approval(continuation_id="cont-1", agent_id="agent-1")
        approval2 = _make_approval(continuation_id="cont-1", agent_id="agent-2")
        await store.save(approval1)
        await store.save(approval2)
        retrieved = await store.get("cont-1")
        assert retrieved is not None
        assert retrieved.agent_id == "agent-2"


class TestRedisApprovalStore:
    @pytest.mark.asyncio
    async def test_replicas_share_approval_and_preserve_payload(self):
        backend = FakeRedisBackend()
        replica_a = RedisApprovalStore(client=FakeRedis(backend), default_ttl_seconds=60)
        replica_b = RedisApprovalStore(client=FakeRedis(backend), default_ttl_seconds=60)
        approval = _make_approval(
            all_tool_results=[{"tool": "read", "output": {"ok": True}}],
            iterations=2,
            request_id="request-1",
            session_id="session-1",
            input_payload={"question": "status"},
        )

        await replica_a.save(approval)
        retrieved = await replica_b.get("cont-1")

        assert retrieved is not None
        assert retrieved.agent_id == approval.agent_id
        assert retrieved.tool_requests == approval.tool_requests
        assert retrieved.messages == approval.messages
        assert retrieved.all_tool_results == approval.all_tool_results
        assert retrieved.iterations == 2
        assert retrieved.request_id == "request-1"
        assert retrieved.session_id == "session-1"
        assert retrieved.input_payload == {"question": "status"}
        await replica_a.aclose()
        await replica_b.aclose()

    @pytest.mark.asyncio
    async def test_expired_and_malformed_records_are_removed(self):
        client = FakeRedis(FakeRedisBackend())
        store = RedisApprovalStore(client=client)
        expired = _make_approval(expires_at=monotonic() - 1)
        await store.save(expired)
        assert await store.get("cont-1") is None

        await client.set(store._key("broken"), "not-json")
        assert await store.get("broken") is None
        assert await client.get(store._key("broken")) is None
        await store.aclose()

    @pytest.mark.asyncio
    async def test_delete_health_and_injected_client_ownership(self):
        client = FakeRedis(FakeRedisBackend())
        store = RedisApprovalStore(client=client)
        await store.save(_make_approval())
        await store.delete("cont-1")
        assert await store.get("cont-1") is None
        assert await store.health_check() is True
        await store.aclose()
        assert client.closed is False

    def test_configuration_is_validated(self):
        with pytest.raises(ValueError, match="Redis session endpoint"):
            RedisApprovalStore("https://approvals.example.test")
        with pytest.raises(ValueError, match="default_ttl_seconds must be positive"):
            RedisApprovalStore(default_ttl_seconds=0, client=FakeRedis(FakeRedisBackend()))
        with pytest.raises(ValueError, match="namespace"):
            RedisApprovalStore(namespace=" bad", client=FakeRedis(FakeRedisBackend()))


class TestApprovalStoreInterface:
    def test_in_memory_store_is_subclass(self):
        assert issubclass(InMemoryApprovalStore, ApprovalStore)

    def test_redis_store_is_subclass(self):
        assert issubclass(RedisApprovalStore, ApprovalStore)

    def test_cannot_instantiate_abstract(self):
        try:
            ApprovalStore()
        except TypeError:
            pass
        else:
            raise AssertionError("should not be instantiable")
