"""Minimal A2A gateway with traffic policy and resilience (C3).

Routes calls to agents by the first path segment —
``/{agent}/{rest...}`` → the route's upstream targets — and adds the C3
policies at the gateway layer while leaving agent-local enforcement
untouched:

- **authentication** through a pluggable ``GatewayAuthenticator`` (static
  tokens mapping to tenant claims by default); unauthenticated calls are
  rejected before routing;
- **authorization** per route via allowed tenants, on top of the verified
  caller claim (the agent still enforces its own policy downstream and the
  original credentials are forwarded end to end);
- **rate limits** as per-tenant token buckets (429 when exhausted);
- **load balancing** as round-robin across a route's ordered targets with
  **fallback** to later targets;
- **circuit breaking** per target — consecutive failures open the target
  for a cooldown, half-open probes close it;
- **bulkheads** as per-target concurrency caps; a saturated target is
  skipped like an open circuit;
- **retries** walk to the *next* target only for safe requests (GET/HEAD)
  or calls carrying an ``idempotency-key`` header — the same
  never-replay-a-side-effect rule the core enforces, applied at the edge.

Everything is in-memory and per-process: the minimal credible C3 form.
The gateway never executes agent logic; it forwards bytes and policy
decisions stay with the agent (C0).
"""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request, Response

_IDEMPOTENCY_HEADER = "idempotency-key"
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
# Response headers that must not be forwarded verbatim: hop-by-hop set plus
# headers the proxy itself recomputes from the forwarded body.
_RESPONSE_STRIP = _HOP_BY_HOP | {"content-length", "content-encoding"}
_MAX_BODY_BYTES = 10 * 1024 * 1024


class GatewayAuthenticationError(RuntimeError):
    """Raised when a call carries no verifiable credentials."""


class Caller:
    """The verified caller claim the gateway acts on."""

    def __init__(self, tenant: str | None = None, subject: str = "anonymous") -> None:
        self.tenant = tenant
        self.subject = subject


class GatewayAuthenticator(Protocol):
    """Edge authentication: headers in, verified caller out."""

    def authenticate(self, headers: Any) -> Caller | None:
        """Return the caller, or None when credentials are missing/invalid."""


class StaticTokenAuthenticator:
    """Bearer-token authentication with token-to-tenant grants.

    ``tokens`` maps an expected bearer token to the caller it represents
    (``token -> (tenant, subject)``); a token absent from the map is
    rejected. Deployments replace this with an OIDC-backed authenticator
    for production; the gateway contract does not change.
    """

    def __init__(self, tokens: dict[str, tuple[str | None, str]]) -> None:
        self._tokens = tokens

    def authenticate(self, headers: Any) -> Caller | None:
        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        # Constant-time comparison: a dict lookup by token would let call
        # timing distinguish prefix matches on the expected tokens.
        for expected, grant in self._tokens.items():
            if hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8")):
                tenant, subject = grant
                return Caller(tenant=tenant, subject=subject)
        return None


@dataclass
class Target:
    """One upstream deployment of an agent."""

    base_url: str
    max_concurrency: int = 16
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    _semaphore: asyncio.Semaphore | None = field(default=None, repr=False)
    _failures: int = field(default=0, repr=False)
    _opened_at: float | None = field(default=None, repr=False)

    def _ensure_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        return self._semaphore

    def acquire(self) -> asyncio.Semaphore:
        """The bulkhead slot semaphore for this target."""
        return self._ensure_semaphore()

    async def try_acquire(self) -> bool:
        """Take a bulkhead slot without waiting; False when saturated."""
        semaphore = self._ensure_semaphore()
        if semaphore.locked():
            return False
        await semaphore.acquire()
        return True

    def release_slot(self) -> None:
        self._ensure_semaphore().release()

    def available(self) -> bool:
        if self._opened_at is None:
            return True
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # Half-open: allow a single probe through the normal path.
            self._opened_at = None
            self._failures = self.failure_threshold - 1
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()


@dataclass
class GatewayRoute:
    """An agent's ordered targets plus its per-route policy."""

    agent: str
    targets: list[Target]
    allowed_tenants: list[str] | None = None  # None = unrestricted
    rate_limit_per_minute: int = 600


class _TokenBucket:
    def __init__(self, rate_per_minute: int) -> None:
        self._capacity = float(rate_per_minute)
        self._tokens = self._capacity
        self._updated = time.monotonic()

    def try_take(self) -> bool:
        now = time.monotonic()
        self._tokens = min(
            self._capacity, self._tokens + (now - self._updated) * (self._capacity / 60.0)
        )
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class Gateway:
    """Routing core: policy checks, target selection, proxied delivery."""

    def __init__(
        self,
        routes: list[GatewayRoute],
        *,
        authenticator: GatewayAuthenticator | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        rate_limit_max_buckets: int = 10_000,
        rate_limit_idle_seconds: float = 300.0,
    ) -> None:
        self._routes = {route.agent: route for route in routes}
        self._authenticator = authenticator
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        # (bucket, last_used monotonic); bounded so distinct-token spraying
        # cannot grow memory without limit.
        self._buckets: dict[tuple[str, str], tuple[_TokenBucket, float]] = {}
        self._rate_limit_max_buckets = rate_limit_max_buckets
        self._rate_limit_idle_seconds = rate_limit_idle_seconds
        self._round_robin: dict[str, int] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def rate_limit_bucket_count(self) -> int:
        """Live rate-limit buckets (bounded; see rate_limit_max_buckets)."""
        return len(self._buckets)

    def breaker_states(self) -> dict[str, dict[str, bool]]:
        return {
            agent: {target.base_url: target.available() for target in route.targets}
            for agent, route in self._routes.items()
        }

    def _authenticate(self, request: Request) -> Caller:
        if self._authenticator is None:
            return Caller()
        caller = self._authenticator.authenticate(request.headers)
        if caller is None:
            raise GatewayAuthenticationError("missing or invalid bearer credentials")
        return caller

    def _authorize(self, route: GatewayRoute, caller: Caller) -> None:
        if route.allowed_tenants is None:
            return
        if caller.tenant is None or caller.tenant not in route.allowed_tenants:
            raise PermissionError(f"tenant '{caller.tenant}' may not call '{route.agent}'")

    def _rate_limit(self, route: GatewayRoute, caller: Caller) -> bool:
        key = (route.agent, caller.tenant or caller.subject)
        now = time.monotonic()
        entry = self._buckets.get(key)
        if entry is None:
            self._evict_buckets(now)
            entry = (_TokenBucket(route.rate_limit_per_minute), now)
            self._buckets[key] = entry
        else:
            self._buckets[key] = (entry[0], now)
        return entry[0].try_take()

    def _evict_buckets(self, now: float) -> None:
        """Make room for a new bucket: idle entries first, then LRU."""
        if len(self._buckets) < self._rate_limit_max_buckets:
            return
        idle_cutoff = now - self._rate_limit_idle_seconds
        idle = [k for k, (_, used) in self._buckets.items() if used <= idle_cutoff]
        for key in idle:
            del self._buckets[key]
        while len(self._buckets) >= self._rate_limit_max_buckets:
            oldest = min(self._buckets.items(), key=lambda item: item[1][1])[0]
            del self._buckets[oldest]

    def _select_targets(self, route: GatewayRoute) -> list[Target]:
        available = [t for t in route.targets if t.available()]
        if not available:
            return []
        start = self._round_robin.get(route.agent, 0) % len(available)
        self._round_robin[route.agent] = start + 1
        return available[start:] + available[:start]

    async def forward(self, request: Request, caller: Caller) -> Response:
        agent = request.path_params["agent"]
        rest = request.path_params.get("path", "")
        route = self._routes.get(agent)
        if route is None:
            return Response(status_code=404, content=f"no route for agent '{agent}'")
        try:
            self._authorize(route, caller)
        except PermissionError as exc:
            return Response(status_code=403, content=str(exc))
        if not self._rate_limit(route, caller):
            return Response(status_code=429, content="rate limit exceeded")

        targets = self._select_targets(route)
        if not targets:
            return Response(status_code=503, content=f"no healthy target for '{agent}'")

        body = await request.body()
        if len(body) > _MAX_BODY_BYTES:
            return Response(status_code=413, content="request body too large")
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _HOP_BY_HOP
        }
        retryable = request.method in {"GET", "HEAD"} or _IDEMPOTENCY_HEADER in headers
        attempts = targets if retryable else targets[:1]
        query = str(request.url.query) or None
        last_status = 503
        last_content: bytes = b"no upstream accepted the call"
        for target in attempts:
            if not await target.try_acquire():
                continue  # bulkhead saturated: skip like an open circuit
            try:
                upstream = await self._client.request(
                    request.method,
                    f"{target.base_url}/{rest}",
                    params=query,
                    headers=headers,
                    content=body,
                )
            except httpx.HTTPError:
                target.record_failure()
                continue
            finally:
                target.release_slot()
            if upstream.status_code >= 500:
                target.record_failure()
                last_status, last_content = upstream.status_code, upstream.content
                continue
            target.record_success()
            return _proxy_response(upstream)
        # Non-retryable failures report exactly what the executed target
        # answered, the same as retryable exhaustion — no generic stand-in.
        return Response(status_code=last_status, content=last_content)


def _proxy_response(upstream: httpx.Response) -> Response:
    """Forward the upstream response with safe headers preserved."""
    forwarded = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in _RESPONSE_STRIP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=forwarded,
    )


def create_gateway_app(gateway: Gateway) -> FastAPI:
    """FastAPI surface: a policy-checked reverse proxy in front of agents."""
    app = FastAPI(title="Micro-Agent Cloud Gateway", version="0.1.0")
    app.state.gateway = gateway

    # Registered before the /{agent}/{path:path} catch-all so the fixed
    # /gateway/health path is reachable instead of being routed as an agent.
    @app.get("/gateway/health")
    async def health() -> dict[str, Any]:
        return {"targets": gateway.breaker_states()}

    @app.api_route(
        "/{agent}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy(agent: str, path: str, request: Request) -> Response:
        del agent, path
        try:
            caller = gateway._authenticate(request)
        except GatewayAuthenticationError as exc:
            return Response(status_code=401, content=str(exc))
        return await gateway.forward(request, caller)

    return app


__all__ = [
    "Caller",
    "Gateway",
    "GatewayAuthenticationError",
    "GatewayAuthenticator",
    "GatewayRoute",
    "StaticTokenAuthenticator",
    "Target",
    "create_gateway_app",
]
