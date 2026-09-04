# ADR 0017: Read-mostly observability aggregation

## Context

C4 calls for cross-agent tracing, cost/usage aggregation, topology, and
audit views. The C0 security model requires audit to be written by the
agent itself, tamper-evident at the source, and the C0 failure stance
requires that losing a control-plane service never costs serving.

## Decision

Agents push telemetry/audit event batches to the plane, which aggregates
read-only views: traces assembled from spans carrying `caller_agent`
attributes, caller→callee topology edges with call counts, per-agent and
per-tenant token/cost rollups, and an append-only tenant-filterable audit
view. The plane validates and aggregates but never mutates or decides; its
store is in-memory for the minimal C4 form. See
[CLOUD_OBSERVABILITY.md](../CLOUD_OBSERVABILITY.md).

## Consequences

- audit remains tamper-evident at the source — the plane can only ever show
  what agents reported, never rewrite it;
- losing the plane degrades visibility only; no serving path depends on it;
- durable, retention-aware storage and plane authentication replace (not
  extend) the in-memory, unauthenticated minimal form in later hardening.
