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
  `StaticTokenAuthenticator` maps configured bearer tokens to caller
  claims; missing or invalid credentials get 401 before any routing;
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

All state is in-memory and per-process — the minimal credible C3 form.
Production deployments add a shared store for breaker/rate state behind the
same interfaces. Streaming pass-through and response-header propagation are
deliberately deferred.

## Verification

10 tests in `tests/test_cloud_gateway.py`: 401/403/429 edges, tenant
authorization, unknown routes, fallback on 5xx, the no-retry rule for
non-idempotent calls, idempotency-key replay to the fallback, breaker
open/half-open with a controlled clock, success-reset, and the
saturated-bulkhead skip.
