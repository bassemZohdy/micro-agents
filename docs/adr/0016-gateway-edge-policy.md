# ADR 0016: Gateway edge policy with side-effect-safe retries

## Context

C3 calls for routing A2A traffic with authentication, authorization, rate
limits, and policy, plus the resilience set at the appropriate layer. The
C0 boundary fixes where enforcement lives: the gateway owns edge policy and
routing; the agent always keeps its own policy enforcement, approvals, and
audit; verified credentials propagate end to end.

## Decision

Implement the gateway as a policy-checked reverse proxy over ordered
per-agent targets: a pluggable edge authenticator (static bearer tokens
first, OIDC later behind the same protocol), per-route tenant
authorization on the verified claim, per-tenant token-bucket rate limits,
round-robin selection with ordered fallbacks, per-target circuit breakers
with half-open probes, per-target bulkheads that skip saturated targets,
and retries confined to safe methods or calls carrying an
`idempotency-key` header. See [CLOUD_GATEWAY.md](../CLOUD_GATEWAY.md).

## Consequences

- a side effect can never replay across targets: non-idempotent calls
  execute on exactly one target, mirroring the core's retry-suppression
  rule at the edge;
- saturated targets shed load instead of accumulating hidden queues;
- all gateway state is per-process memory — acceptable for the minimal C3
  form, with shared-state backends as the first production hardening;
- the agent's own auth/policy still runs on forwarded original credentials,
  so removing the gateway never lowers an agent's enforcement.
