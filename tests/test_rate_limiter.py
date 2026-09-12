"""Built-in HTTP rate limiter tests."""

from types import SimpleNamespace

from micro_agent.interoperability import InMemoryRateLimitStore, TokenBucketRateLimiter


def _request(caller: str = "caller-1") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            identity=SimpleNamespace(
                caller=SimpleNamespace(caller_id=caller),
                user=SimpleNamespace(tenant_id="tenant-a"),
            )
        )
    )


def test_token_bucket_returns_stable_decisions_and_isolates_callers():
    limiter = TokenBucketRateLimiter(2, burst=2)
    assert limiter.check(_request()).allowed
    assert limiter.check(_request()).allowed
    rejected = limiter.check(_request())
    assert not rejected.allowed
    assert rejected.limit == 2
    assert rejected.remaining == 0
    assert rejected.retry_after_seconds >= 1
    assert limiter.check(_request("caller-2")).allowed


def test_rate_limit_store_bounds_idle_buckets():
    store = InMemoryRateLimitStore(max_buckets=2, idle_seconds=1)
    store.consume("a", rate_per_second=1, capacity=1, now=1.0)
    store.consume("b", rate_per_second=1, capacity=1, now=1.0)
    store.consume("c", rate_per_second=1, capacity=1, now=1.0)
    assert store.bucket_count() == 2
    store.consume("c", rate_per_second=1, capacity=1, now=3.0)
    assert store.bucket_count() == 1
