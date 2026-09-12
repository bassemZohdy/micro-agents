# Cloud Gateway and Resilience (C3)

The minimal A2A gateway, implementing the C3 backlog items on the C0
boundary ([architecture](architecture/CLOUD_ARCHITECTURE.md),
[ADR 0013](adr/0013-cloud-control-plane-boundary.md)). Code: the top-level
`cloud` package (`cloud.gateway`); the core framework never imports it, and
the gateway never executes agent logic — it forwards bytes and policy
decisions stay with each agent.

## Routing and policy

Routes map an agent name to ordered upstream targets:
`/{agent}/{rest...}` is forwarded to `target/{rest}`. At the edge:

- **authentication** through the `GatewayAuthenticator` protocol —
  `StaticTokenAuthenticator` maps configured bearer tokens to caller claims and
  `OidcGatewayAuthenticator` validates asymmetric OIDC JWTs against issuer,
  audience, required claims, and an injectable/cached JWKS client; missing or
  invalid credentials get 401 before any routing;
- **authorization** per route via `allowed_tenants` on the verified caller
  claim (403 otherwise). Original credentials are forwarded untouched, so
  the agent's own auth and policy enforcement still run end to end;
- **rate limits** as per-tenant token buckets per route (429 when the
  bucket is empty; different tenants never share buckets).

## Resilience set

- **load balancing**: round-robin across a route's targets;
- **fallback**: the next healthy target receives the call when an earlier
  one fails;
- **circuit breaking** per target: `failure_threshold` consecutive
  transport/5xx failures open the target for `cooldown_seconds`; a
  half-open probe then decides closure (`GET /gateway/health` reports
  breaker state);
- **bulkheads** per target: a concurrency cap; a saturated target is
  *skipped*, never queued — no target accumulates hidden work;
- **retries** walk to the *next* target and only for safe calls (GET/HEAD)
  or calls carrying an `idempotency-key` header — the core's
  never-replay-a-side-effect rule applied at the edge; a failed
  non-idempotent POST is returned as-is from its single execution target.

The default state is in-memory and per-process — the minimal local C3 form.
For independently scaled gateways, inject `RedisGatewayStateStore` from the
optional `redis` extra. Its atomic, TTL-bounded scripts coordinate per-route
rate-limit buckets, per-target breaker failures/half-open probes, and
expiring bulkhead leases across workers. Requests remain bounded at 10 MB before forwarding; accepted
`text/event-stream` responses use a streaming response path so the gateway
does not buffer the upstream event body, and the target bulkhead slot remains
held until the stream completes or disconnects.
Successful upstream responses preserve safe end-to-end headers; hop-by-hop,
content-length, and content-encoding headers are stripped before forwarding.

## Verification

18 unit tests in `tests/test_cloud_gateway.py` plus the shared-state integration
case in `tests/test_cloud_gateway_state.py`: 401/403/429 edges, tenant
authorization, health routing, query forwarding, unknown routes, fallback on
5xx, the no-retry rule for non-idempotent calls, idempotency-key replay to the
fallback, safe response-header propagation, bounded rate-limit state,
constant-time token comparison, breaker open/half-open with a controlled
clock, success-reset, saturated-bulkhead skip, OIDC claim validation,
event-stream pass-through, and two-client Redis coordination.
