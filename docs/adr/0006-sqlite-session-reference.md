# ADR 0006 — SQLite Session Provider for Development Persistence

Date: 2026-08-30 · Status: Accepted

## Context

A persistent provider is useful for local development and contract testing;
only an in-memory provider previously existed. Production replicas require an
external service with explicit concurrency and availability guarantees.

## Decision

`SqliteSessionProvider` (standard library only) is the reference persistent
provider: sessions are rows with created/expires timestamps, expiration is
checked on read, and updates refresh expiry. Its tests prove two provider
objects can share one local database file; they do not prove multi-process or
Kubernetes multi-replica correctness.

## Consequences

- Acceptance is demonstrable without external infrastructure.
- SQLite is limited to development until access is explicitly serialized and
  its supported process/filesystem model is documented.
- Production deployments need a `SessionProvider` backed by a service such as
  PostgreSQL or Redis, with concurrency and tenant-isolation tests.
