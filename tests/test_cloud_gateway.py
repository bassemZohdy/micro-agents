"""Cloud C3 tests: gateway routing, policy, and resilience."""

from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from cloud.gateway import (
    Caller,
    Gateway,
    GatewayRoute,
    StaticTokenAuthenticator,
    Target,
    create_gateway_app,
)


def _make_gateway(
    routes: list[GatewayRoute],
    handler,
    *,
    tokens: dict[str, tuple[str | None, str]] | None = None,
) -> Gateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    authenticator = StaticTokenAuthenticator(tokens) if tokens is not None else None
    return Gateway(
        routes,
        authenticator=authenticator,
        client=client,
    )


def _default_tokens() -> dict[str, tuple[str | None, str]]:
    return {
        "acme-token": ("acme", "alice"),
        "other-token": ("other", "bob"),
        "anon-token": (None, "anon"),
    }


def _routes() -> list[GatewayRoute]:
    return [
        GatewayRoute(
            agent="greeter",
            targets=[
                Target(base_url="http://primary.test"),
                Target(base_url="http://fallback.test"),
            ],
        )
    ]


class TestAuthNAndAuthZ:
    def test_missing_or_invalid_credentials_are_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            assert http.get("/greeter/hello").status_code == 401
            assert (
                http.get("/greeter/hello", headers={"Authorization": "Bearer wrong"}).status_code
                == 401
            )

    async def test_valid_credentials_pass_and_tenant_is_known(self):
        authenticator = StaticTokenAuthenticator(_default_tokens())
        caller = authenticator.authenticate(httpx.Headers({"Authorization": "Bearer acme-token"}))
        assert caller is not None
        assert (caller.tenant, caller.subject) == ("acme", "alice")
        assert authenticator.authenticate(httpx.Headers({"Authorization": "Bearer nope"})) is None
        assert authenticator.authenticate(httpx.Headers()) is None

    def test_tenant_not_allowed_on_route_is_forbidden(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        routes = [
            GatewayRoute(
                agent="greeter",
                targets=[Target(base_url="http://primary.test")],
                allowed_tenants=["acme"],
            )
        ]
        gateway = _make_gateway(routes, handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            ok = http.get("/greeter/hello", headers={"Authorization": "Bearer acme-token"})
            assert ok.status_code == 200
            forbidden = http.get("/greeter/hello", headers={"Authorization": "Bearer other-token"})
            assert forbidden.status_code == 403


class TestRateLimitAndRouting:
    def test_rate_limit_returns_429_when_bucket_empty(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        routes = [
            GatewayRoute(
                agent="greeter",
                targets=[Target(base_url="http://primary.test")],
                rate_limit_per_minute=2,
            )
        ]
        gateway = _make_gateway(routes, handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            headers = {"Authorization": "Bearer acme-token"}
            assert http.get("/greeter/a", headers=headers).status_code == 200
            assert http.get("/greeter/b", headers=headers).status_code == 200
            limited = http.get("/greeter/c", headers=headers)
            assert limited.status_code == 429
            # A different tenant has its own bucket.
            assert (
                http.get("/greeter/d", headers={"Authorization": "Bearer other-token"}).status_code
                == 200
            )

    async def test_unknown_agent_is_404_and_safe_calls_retry_to_fallback(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.host or "")
            if request.url.host == "primary.test":
                return httpx.Response(503, json={"detail": "overloaded"})
            return httpx.Response(200, json={"served_by": "fallback"})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            missing = http.get("/nosuch/hello", headers={"Authorization": "Bearer acme-token"})
            assert missing.status_code == 404

            seen.clear()
            ok = http.get("/greeter/hello", headers={"Authorization": "Bearer acme-token"})
            assert ok.status_code == 200
            assert ok.json()["served_by"] == "fallback"
            assert "primary.test" in seen and "fallback.test" in seen

    async def test_unsafe_post_without_key_is_not_retried(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host or "")
            return httpx.Response(500, json={"detail": "boom"})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.post(
                "/greeter/invoke", json={"x": 1}, headers={"Authorization": "Bearer acme-token"}
            )
            assert response.status_code == 500
            # A side-effect call must execute on exactly one target.
            assert calls.count("primary.test") == 1
            assert "fallback.test" not in calls

    async def test_unsafe_post_with_idempotency_key_may_replay_to_fallback(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host or "")
            if request.url.host == "primary.test":
                return httpx.Response(503, json={"detail": "overloaded"})
            return httpx.Response(200, json={"served_by": "fallback"})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.post(
                "/greeter/invoke",
                json={"x": 1},
                headers={
                    "Authorization": "Bearer acme-token",
                    "idempotency-key": "op-123",
                },
            )
            assert response.status_code == 200
            assert response.json()["served_by"] == "fallback"


class TestCircuitBreakerAndBulkhead:
    async def test_open_circuit_skips_target_until_cooldown(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr("cloud.gateway.time.monotonic", lambda: clock[0])
        primary = Target(base_url="http://primary.test", failure_threshold=2)
        fallback = Target(base_url="http://fallback.test")
        route = GatewayRoute(agent="greeter", targets=[primary, fallback])
        gateway = Gateway(routes=[route])

        for _ in range(2):
            primary.record_failure()
        assert not primary.available()
        # Selection skips the open target entirely.
        selected = gateway._select_targets(route)
        assert [t.base_url for t in selected] == ["http://fallback.test"]

        clock[0] += 31
        assert primary.available()  # half-open probe allowed after cooldown

    async def test_success_resets_failure_count(self):
        target = Target(base_url="http://primary.test", failure_threshold=2)
        target.record_failure()
        target.record_success()
        target.record_failure()
        assert target.available()

    async def test_saturated_bulkhead_is_skipped_not_queued(self):
        parked = asyncio.Event()
        release = asyncio.Event()
        entered = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal entered
            entered += 1
            parked.set()
            await release.wait()
            return httpx.Response(200, json={"ok": True})

        gateway = Gateway(
            routes=[
                GatewayRoute(
                    agent="greeter",
                    targets=[Target(base_url="http://busy.test", max_concurrency=1)],
                )
            ]
        )
        gateway._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        class _Req:
            path_params = {"agent": "greeter", "path": "x"}
            method = "GET"
            headers = httpx.Headers()
            url = httpx.URL("http://gateway.test/greeter/x")

            async def body(self) -> bytes:
                return b""

        first = asyncio.ensure_future(gateway.forward(_Req(), Caller()))
        await asyncio.wait_for(parked.wait(), timeout=2)
        # The single bulkhead slot is held: the second call is rejected
        # immediately instead of queueing behind the first.
        second_response = await gateway.forward(_Req(), Caller())
        release.set()
        first_response = await first
        assert first_response.status_code == 200
        assert second_response.status_code == 503
        assert entered == 1


class TestGatewayHardening:
    """Hardening fixes from the 2026-09-04 cloud review."""

    def test_gateway_health_route_is_reachable(self):
        """`/gateway/health` must not be swallowed by the /{agent}/... catch-all."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.get("/gateway/health")
            assert response.status_code == 200
            assert "greeter" in response.json()["targets"]

    def test_query_string_is_forwarded_to_upstream(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.query.decode())
            return httpx.Response(200, json={"ok": True})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.get(
                "/greeter/search?q=x&p=2",
                headers={"Authorization": "Bearer acme-token"},
            )
            assert response.status_code == 200
            assert seen == ["q=x&p=2"]

        # A call without a query string must not send an empty "?".
        with TestClient(create_gateway_app(gateway)) as http:
            http.get("/greeter/plain", headers={"Authorization": "Bearer acme-token"})
            assert seen[-1] == ""

    def test_non_retryable_failure_returns_executed_target_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "boom", "target": "primary"})

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.post(
                "/greeter/invoke",
                json={"x": 1},
                headers={"Authorization": "Bearer acme-token"},
            )
        # Consistent with single-target routes: the executed target's actual
        # response, not a generic stand-in message.
        assert response.status_code == 500
        assert response.json() == {"detail": "boom", "target": "primary"}

    def test_safe_upstream_headers_are_propagated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"ok": True},
                headers={
                    "x-request-id": "up-123",
                    "content-type": "application/json",
                    "content-length": "999",  # must be recomputed, not forwarded
                },
            )

        gateway = _make_gateway(_routes(), handler, tokens=_default_tokens())
        with TestClient(create_gateway_app(gateway)) as http:
            response = http.get("/greeter/hello", headers={"Authorization": "Bearer acme-token"})
        assert response.status_code == 200
        assert response.headers["x-request-id"] == "up-123"
        assert response.headers["content-type"].startswith("application/json")
        # The forwarded content-length (999) cannot match the real body.
        assert int(response.headers["content-length"]) != 999

    def test_rate_limit_buckets_are_bounded(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr("cloud.gateway.time.monotonic", lambda: clock[0])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        tokens = {f"token-{i}": (f"tenant-{i}", "subject") for i in range(6)}
        routes = [GatewayRoute(agent="greeter", targets=[Target(base_url="http://t.test")])]
        gateway = _make_gateway(routes, handler, tokens=tokens)
        gateway._rate_limit_max_buckets = 3
        gateway._rate_limit_idle_seconds = 60.0

        with TestClient(create_gateway_app(gateway)) as http:
            for i in range(6):
                response = http.get(
                    "/greeter/x",
                    headers={"Authorization": f"Bearer token-{i}"},
                )
                assert response.status_code == 200
                assert gateway.rate_limit_bucket_count() <= 3

        # Idle eviction: advance past the idle window and touch one key —
        # the stale buckets are reclaimable.
        clock[0] += 120.0
        assert gateway._rate_limit(routes[0], Caller(tenant="tenant-0"))
        assert gateway.rate_limit_bucket_count() <= 3

    def test_token_comparison_is_constant_time_correct(self):
        authenticator = StaticTokenAuthenticator(
            {"alpha-token": ("a", "s1"), "beta-token": ("b", "s2")}
        )
        # Correct tokens still authenticate; unknown same-length tokens do not.
        assert (
            authenticator.authenticate(httpx.Headers({"Authorization": "Bearer alpha-token"}))
            is not None
        )
        assert (
            authenticator.authenticate(httpx.Headers({"Authorization": "Bearer beta-token"}))
            is not None
        )
        assert (
            authenticator.authenticate(httpx.Headers({"Authorization": "Bearer gamma-token"}))
            is None
        )
        assert authenticator.authenticate(httpx.Headers({"Authorization": "Bearer "})) is None
