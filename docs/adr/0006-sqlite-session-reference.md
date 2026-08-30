# ADR 0006 — SQLite Session Provider as Persistent Reference

Date: 2026-08-30 · Status: Accepted

## Context

M11 requires multiple replicas to share persistent session state; only an
in-memory provider existed.

## Decision

`SqliteSessionProvider` (standard library only) is the reference persistent
provider: sessions are rows with created/expires timestamps, expiration is
checked on read, and updates refresh expiry. Multi-replica behavior is proven
by tests sharing one database file.

## Consequences

- Acceptance is demonstrable without external infrastructure.
- Production deployments can implement `SessionProvider` against Redis/Postgres
  following the same contract.
