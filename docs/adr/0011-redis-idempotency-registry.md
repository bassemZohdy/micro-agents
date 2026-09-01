# ADR 0011: Redis operation and idempotency registry

## Status

Accepted for the custom reference runtime. The Google ADK adapter does not map
this provider yet and rejects the binding during bootstrap.

## Context

The in-memory `OperationRegistry` prevents duplicate side effects only inside a
single process. A multi-replica service needs one shared reservation and result
store so retries routed to another replica observe the original operation. The
provider must remain optional: local development should not require Redis, and
the runtime-neutral contracts must not expose a Redis client.

## Decision

Add `RedisOperationRegistry` behind `OperationRegistryProtocol` and select it
from `MICRO_AGENT_IDEMPOTENCY_ENDPOINT` when the custom runtime is built. Redis
and Redis-over-TLS endpoints are accepted when the optional `redis` extra is
installed; unsupported schemes fail before runtime creation.

For an idempotency key, the registry performs an atomic Redis `SET` with
`NX` and `EX` options. The winner owns an in-progress reservation. A losing
replica receives either the completed `OperationResult` or an explicit
in-progress result. Completion persists the result with the same TTL and is
accepted only when the operation still owns the reservation. Malformed values
are discarded rather than surfaced as an untrusted result. Health checks use
`PING`, and shutdown closes only clients created by the provider.

The runtime uses the registry's claim path before executing side-effect tools,
records success or failure afterward, and exposes the provider through startup
and readiness probes. Existing injected registries and the local in-memory
implementation remain supported through the protocol's compatibility fallback.

## Consequences

Positive:

- retries can be deduplicated across independently scheduled custom-runtime
  replicas;
- reservation and result expiry bound stale state without a cleanup worker;
- Redis remains an opt-in dependency and tests can inject a compatible client;
- provider health and lifecycle behavior are explicit at startup and shutdown.

Trade-offs and remaining work:

- operation keys are namespaced by verified tenant when present; local or
  unverified calls retain a legacy provider-wide namespace, and session/memory
  session/memory snapshots now carry the same tenant boundary and optimistic
  version checks;
- completion uses an ownership check before persistence, so callers must still
  choose a TTL long enough for the expected operation duration;
- Google ADK idempotency mapping, durable knowledge, and production approval
  state remain open work.

## Alternatives considered

- Keep idempotency process-local: rejected because retries can land on another
  replica and repeat side effects.
- Add a mandatory PostgreSQL dependency: rejected for the reference framework's
  lightweight install and existing optional-provider model.
- Hide idempotency inside session or memory providers: rejected because
  operation ownership and side-effect replay semantics are distinct from
  conversation history and retrieval records.
