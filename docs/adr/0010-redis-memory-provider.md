# ADR 0010: Redis memory provider for shared retention

## Context

The built-in memory provider is process-local and cannot support independently
scaled agents. Redis is already an optional shared-state dependency for
sessions, so using it for memory avoids adding a second backend while keeping
the memory and session namespaces separate.

## Decision

Provide `RedisMemoryProvider` behind the optional `redis` extra. Each memory
record is a scoped JSON value indexed in a namespaced sorted set. Redis key TTLs
enforce expiry across processes; reads and capacity checks purge stale index
members. `MemoryPolicy` controls TTL and maximum retained records, and the
provider exposes `health_check()` plus ownership-aware `aclose()`.

`MICRO_AGENT_MEMORY_ENDPOINT` selects Redis for a declared memory dependency
when it uses `redis://` or `rediss://`. Other endpoint schemes fail before
runtime creation rather than silently using process-local memory.

## Consequences

- independently scaled processes share declared memory records;
- memory and session data use separate Redis key namespaces;
- values are JSON-serialized and non-JSON values use their string form;
- entries are tenant-scoped when a verified tenant is available and carry
  optimistic versions; stale non-zero-version writes raise `StateConflictError`.
