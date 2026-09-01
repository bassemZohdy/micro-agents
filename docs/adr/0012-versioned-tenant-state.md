# ADR 0012: Versioned tenant-scoped session and memory state

## Status

Accepted

## Context

Session and memory records can be shared by independent runtime processes. A
provider-wide key lets one verified tenant read or overwrite another tenant's
state, and last-write-wins updates silently discard concurrent changes.

## Decision

Add an optional `tenant_id` and a monotonic `version` to `SessionContext`,
`SessionMetadata`, and `MemoryEntry`.

- Verified invocation identity supplies `tenant_id` to runtime reads and writes.
- Providers use tenant-aware storage keys; an unscoped call retains the legacy
  provider-wide namespace for compatibility.
- New records start at version 1. A provider read returns a snapshot carrying
  its current version; writing a non-zero-version snapshot requires that the
  stored version still matches and then increments it.
- A missing record accepts a zero-version write and starts at version 1.
- A mismatch raises `StateConflictError`, allowing callers to reload, merge,
  and retry without losing an update.
- Redis providers use `WATCH`/`MULTI`/`EXEC` for versioned writes; SQLite stays
  a serialized single-process development provider.

## Consequences

Positive:

- Tenant records are isolated across in-memory, SQLite, and Redis providers.
- Concurrent Redis updates fail explicitly instead of silently overwriting one
  another.
- Existing callers that construct zero-version records keep unconditional
  write behavior while migrating to version-aware updates.

Trade-offs and remaining work:

- Callers must decide how to merge a `StateConflictError`.
- Unverified/local calls intentionally retain the legacy provider-wide scope.
- Google ADK idempotency mapping and durable knowledge remain separate backlog
  items.
