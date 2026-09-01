# ADR 0009: Redis session provider for shared state

## Context

The in-memory and SQLite providers are useful for development but cannot be
the source of truth for independently scaled service processes. The session
SPI already has the lifecycle needed by a shared backend, while adding a
database-specific dependency to the base install would make the smallest
development path heavier.

## Decision

Provide `RedisSessionProvider` behind the optional `redis` extra. The provider
stores JSON session documents under a namespaced key, maintains a sorted-set
index for active-session listing, and updates the document/index in a Redis
transactional pipeline. Redis key TTLs enforce expiration even when no process
reads the session. The provider accepts `redis://` and `rediss://` endpoints,
exposes `health_check()`, and closes only clients it created itself.

`persistence: external` selects this provider at bootstrap when
`MICRO_AGENT_SESSION_ENDPOINT` is a Redis endpoint. Other external schemes fail
before runtime creation rather than silently falling back to local state.

## Consequences

- independently scaled processes can share session state through Redis;
- the base package remains dependency-light and fake/SQLite development paths
  remain available;
- update conflicts are still last-write-wins; optimistic versioning and tenant
  isolation remain separate P1.5 work;
- memory and idempotency providers are not implicitly made distributed.
