"""Tests for Micro-Agent Safe Side Effects."""

import asyncio

import pytest

from micro_agent.observability import (
    Operation,
    OperationRegistry,
    OperationResult,
    RedisOperationRegistry,
    RetryClassification,
)
from tests.fake_redis import FakeRedis, FakeRedisBackend


class TestRetryClassification:
    """Test retry classification."""

    def test_values(self):
        assert RetryClassification.SAFE == "safe"
        assert RetryClassification.UNSAFE == "unsafe"
        assert RetryClassification.IDEMPOTENT == "idempotent"


class TestOperation:
    """Test operation model."""

    def test_defaults(self):
        op = Operation()
        assert op.operation_id
        assert op.idempotency_key is None
        assert op.retry_classification == RetryClassification.SAFE

    def test_with_idempotency_key(self):
        op = Operation(idempotency_key="key-123", name="payment")
        assert op.idempotency_key == "key-123"
        assert op.name == "payment"

    def test_tenant_is_optional(self):
        op = Operation(idempotency_key="key-123", tenant_id="tenant-a")
        assert op.tenant_id == "tenant-a"

    def test_requires_approval(self):
        op = Operation(requires_approval=True)
        assert op.requires_approval is True


class TestOperationResult:
    """Test operation result."""

    def test_success(self):
        result = OperationResult(operation_id="op-1", status="success")
        assert result.error is None
        assert result.was_deduplicated is False

    def test_deduplicated(self):
        result = OperationResult(was_deduplicated=True)
        assert result.was_deduplicated is True


class TestOperationRegistry:
    """Test operation registry for deduplication."""

    def test_record_and_find(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        result = OperationResult(operation_id=op.operation_id, status="success")
        registry.record(op, result)
        found = registry.find_by_idempotency_key("key-1")
        assert found is not None
        assert found.status == "success"

    def test_not_found(self):
        registry = OperationRegistry()
        assert registry.find_by_idempotency_key("missing") is None

    def test_is_duplicate(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        result = OperationResult(operation_id=op.operation_id, status="success")
        registry.record(op, result)
        assert registry.is_duplicate(op) is True

    def test_not_duplicate(self):
        registry = OperationRegistry()
        op = Operation(idempotency_key="key-1", name="payment")
        assert registry.is_duplicate(op) is False

    def test_no_idempotency_key(self):
        registry = OperationRegistry()
        op = Operation(name="payment")
        assert registry.is_duplicate(op) is False


class TestRedisOperationRegistry:
    """Redis reservations prevent duplicate side effects across replicas."""

    @pytest.mark.asyncio
    async def test_claim_is_atomic_and_completed_result_is_shared(self):
        backend = FakeRedisBackend()
        replica_a = RedisOperationRegistry(client=FakeRedis(backend), ttl_seconds=60)
        replica_b = RedisOperationRegistry(client=FakeRedis(backend), ttl_seconds=60)
        operation_a = Operation(idempotency_key="payment-1", name="payment")
        operation_b = Operation(idempotency_key="payment-1", name="payment")

        claims = await asyncio.gather(replica_a.claim(operation_a), replica_b.claim(operation_b))
        assert sum(claimed for claimed, _prior in claims) == 1
        winner_registry, winner_operation, loser_registry, loser_claim = (
            (replica_a, operation_a, replica_b, claims[1])
            if claims[0][0]
            else (replica_b, operation_b, replica_a, claims[0])
        )
        claimed, pending = loser_claim
        assert claimed is False
        assert pending is not None
        assert pending.status == "in_progress"

        await winner_registry.record(
            winner_operation,
            OperationResult(operation_id=winner_operation.operation_id, output={"receipt": "r-1"}),
        )
        prior = await loser_registry.find_by_idempotency_key("payment-1")
        assert prior is not None
        assert prior.output == {"receipt": "r-1"}
        assert prior.status == "success"
        await replica_a.aclose()
        await replica_b.aclose()

    @pytest.mark.asyncio
    async def test_injected_client_is_not_closed(self):
        client = FakeRedis(FakeRedisBackend())
        registry = RedisOperationRegistry(client=client)
        assert await registry.health_check() is True
        await registry.aclose()
        assert client.closed is False

    @pytest.mark.asyncio
    async def test_tenant_scopes_same_idempotency_key(self):
        backend = FakeRedisBackend()
        tenant_a = RedisOperationRegistry(client=FakeRedis(backend))
        tenant_b = RedisOperationRegistry(client=FakeRedis(backend))
        operation_a = Operation(idempotency_key="payment-1", tenant_id="tenant-a")
        operation_b = Operation(idempotency_key="payment-1", tenant_id="tenant-b")

        try:
            assert await tenant_a.claim(operation_a) == (True, None)
            assert await tenant_b.claim(operation_b) == (True, None)
            await tenant_a.record(
                operation_a,
                OperationResult(operation_id=operation_a.operation_id, output={"tenant": "a"}),
            )
            tenant_b_result = await tenant_b.find_by_idempotency_key("payment-1", "tenant-b")
            assert tenant_b_result is not None
            assert tenant_b_result.status == "in_progress"
            assert tenant_b_result.operation_id == operation_b.operation_id
            assert (await tenant_a.find_by_idempotency_key("payment-1", "tenant-a")).output == {
                "tenant": "a"
            }
        finally:
            await tenant_a.aclose()
            await tenant_b.aclose()

    def test_endpoint_and_ttl_are_validated(self):
        with pytest.raises(ValueError, match="Redis session endpoint"):
            RedisOperationRegistry("https://operations.example.test")
        with pytest.raises(ValueError, match="positive integer"):
            RedisOperationRegistry(ttl_seconds=0, client=FakeRedis(FakeRedisBackend()))
