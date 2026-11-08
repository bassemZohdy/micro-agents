"""Unit coverage for the Redis idempotency registry using the shared fake."""

import pytest

from micro_agent.security import Operation, OperationResult, RedisOperationRegistry
from tests.fake_redis import FakeRedis, FakeRedisBackend

ENDPOINT = "redis://redis.example.test/0"
PREFIX = "ops-test:operation:"


def _registry(backend: FakeRedisBackend | None = None, **kwargs) -> RedisOperationRegistry:
    backend = backend or FakeRedisBackend()
    return RedisOperationRegistry(
        ENDPOINT, namespace="ops-test", client=FakeRedis(backend), **kwargs
    )


@pytest.mark.parametrize("ttl", [0, -1, True, 1.5, "60"])
def test_ttl_must_be_a_positive_integer(ttl):
    with pytest.raises(ValueError, match="ttl_seconds"):
        _registry(ttl_seconds=ttl)


@pytest.mark.parametrize("namespace", ["", " x", "x "])
def test_namespace_must_be_trimmed_and_non_empty(namespace):
    with pytest.raises(ValueError, match="namespace"):
        RedisOperationRegistry(ENDPOINT, namespace=namespace, client=FakeRedis(FakeRedisBackend()))


def test_endpoint_scheme_is_validated():
    with pytest.raises(ValueError):
        RedisOperationRegistry("http://nope", client=FakeRedis(FakeRedisBackend()))


@pytest.mark.asyncio
async def test_operations_without_idempotency_key_are_never_tracked():
    registry = _registry()
    operation = Operation(name="no-key")
    assert await registry.claim(operation) == (True, None)
    assert await registry.claim(operation) == (True, None)
    assert await registry.is_duplicate(operation) is False
    await registry.record(operation, OperationResult(operation_id=operation.operation_id))


@pytest.mark.asyncio
async def test_claim_then_duplicate_then_completed_result_is_replayed():
    registry = _registry()
    first = Operation(idempotency_key="k", name="pay")
    second = Operation(idempotency_key="k", name="pay")

    assert await registry.claim(first) == (True, None)
    assert await registry.is_duplicate(second) is True
    claimed, pending = await registry.claim(second)
    assert claimed is False and pending is not None
    assert (pending.status, pending.was_deduplicated) == ("in_progress", True)

    await registry.record(
        first, OperationResult(operation_id=first.operation_id, output={"receipt": 1})
    )
    claimed, done = await registry.claim(second)
    assert claimed is False and done is not None
    assert done.output == {"receipt": 1}
    assert done.was_deduplicated is True


@pytest.mark.asyncio
async def test_tenants_have_independent_idempotency_namespaces():
    registry = _registry()
    a = Operation(idempotency_key="k", name="x", tenant_id="tenant/a")
    b = Operation(idempotency_key="k", name="x", tenant_id="tenant-b")
    assert await registry.claim(a) == (True, None)
    assert await registry.claim(b) == (True, None)
    assert await registry.find_by_idempotency_key("k", "tenant/a") is not None
    assert await registry.find_by_idempotency_key("k") is None


@pytest.mark.asyncio
async def test_late_result_from_a_stale_owner_is_ignored():
    registry = _registry()
    owner = Operation(idempotency_key="k", name="x")
    intruder = Operation(idempotency_key="k", name="x")
    assert await registry.claim(owner) == (True, None)

    await registry.record(intruder, OperationResult(operation_id=intruder.operation_id))
    current = await registry.find_by_idempotency_key("k")
    assert current is not None
    assert current.operation_id == owner.operation_id
    assert current.status == "in_progress"

    # A result for a key that was never claimed (or expired) is also dropped.
    stranger = Operation(idempotency_key="never", name="x")
    await registry.record(stranger, OperationResult(operation_id=stranger.operation_id))
    assert await registry.find_by_idempotency_key("never") is None


@pytest.mark.asyncio
async def test_corrupt_payload_is_discarded_and_treated_as_absent():
    backend = FakeRedisBackend()
    registry = _registry(backend)
    for raw in ("{not json", "[1]", '{"operation_id": "x"}'):
        backend.values[f"{PREFIX}bad"] = raw
        assert await registry.find_by_idempotency_key("bad") is None
        assert f"{PREFIX}bad" not in backend.values


@pytest.mark.asyncio
async def test_reservation_that_expires_mid_claim_is_retried_once():
    class RacingClient(FakeRedis):
        """First SET NX loses, then the winning key vanishes before GET."""

        def __init__(self, backend):
            super().__init__(backend)
            self.attempts = 0

        async def set(self, key, value, *, ex=None, nx=False):
            self.attempts += 1
            if self.attempts == 1:
                return False  # someone else "holds" it, but it has already expired
            return await super().set(key, value, ex=ex, nx=nx)

    client = RacingClient(FakeRedisBackend())
    registry = RedisOperationRegistry(ENDPOINT, namespace="ops-test", client=client)
    assert await registry.claim(Operation(idempotency_key="k", name="x")) == (True, None)
    assert client.attempts == 2


@pytest.mark.asyncio
async def test_persistent_contention_without_a_readable_result_reports_in_progress():
    class AlwaysTaken(FakeRedis):
        async def set(self, key, value, *, ex=None, nx=False):
            return False

    registry = RedisOperationRegistry(
        ENDPOINT, namespace="ops-test", client=AlwaysTaken(FakeRedisBackend())
    )
    claimed, prior = await registry.claim(Operation(idempotency_key="k", name="x"))
    assert claimed is False
    assert prior is not None
    assert (prior.status, prior.was_deduplicated) == ("in_progress", True)


@pytest.mark.asyncio
async def test_ttl_is_set_on_reservation_and_result():
    backend = FakeRedisBackend()
    registry = _registry(backend, ttl_seconds=120)
    operation = Operation(idempotency_key="k", name="x")
    await registry.claim(operation)
    assert f"{PREFIX}k" in backend.expiry
    backend.expiry.clear()
    await registry.record(operation, OperationResult(operation_id=operation.operation_id))
    assert f"{PREFIX}k" in backend.expiry


@pytest.mark.asyncio
async def test_health_and_close_semantics():
    registry = _registry()
    assert await registry.health_check() is True
    await registry.aclose()  # injected client: left open
    assert registry._client.closed is False

    closed: list[int] = []

    class Owned(FakeRedis):
        async def aclose(self) -> None:
            closed.append(1)

    owned = _registry()
    owned._client = Owned(FakeRedisBackend())
    owned._owns_client = True
    await owned.aclose()
    await owned.aclose()
    assert closed == [1]
