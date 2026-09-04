# Cloud Distributed Observability (C4)

The aggregation plane for cross-agent traces, cost/usage, topology, and
audit views, implementing the C4 backlog items on the C0 boundary
([architecture](architecture/CLOUD_ARCHITECTURE.md),
[ADR 0013](adr/0013-cloud-control-plane-boundary.md)). Code: the top-level
`cloud` package (`cloud.observability`); the core never imports it.

## Model

Agents keep writing telemetry and audit **locally** (tamper-evident at the
source) and push batches to the plane, which aggregates four views:

- **traces** — spans grouped by `trace_id` and ordered; spans cross agents
  when a child span names its `caller_agent`;
- **topology** — caller→callee edges between agents with call counts,
  derived from those `caller_agent` attributes (self-spans create no edge);
- **cost/usage** — `input_tokens`, `output_tokens`, and `cost_usd` rolled
  up per agent with grand totals, filterable by tenant;
- **audit** — an append-only view of audit events (action, decision,
  agent, tenant), newest first, tenant-filterable and bounded by `limit`.

The plane is read-mostly by construction: it can answer what happened, it
cannot change it, and losing it costs visibility, never agents (the C0
failure stance). The in-memory store is the minimal C4 form; a durable,
retention-aware backend replaces it wholesale later.

## HTTP surface

`create_observability_app` (unauthenticated; edge auth rides the C3
gateway):

| Route | Purpose |
| --- | --- |
| `POST /observability/events` | batch ingest (`{"events": [...]}`, max 1000; 422 on invalid) |
| `GET /observability/traces/{trace_id}` | ordered cross-agent spans (404 when unknown) |
| `GET /observability/topology` | caller→callee edges with call counts |
| `GET /observability/costs?tenant=&agent=` | usage/cost rollups |
| `GET /observability/audit?tenant=&limit=` | newest audit events |
| `GET /health/ready` | plane readiness |

## Verification

6 tests in `tests/test_cloud_observability.py` cover invalid-event
rejection, cross-agent trace assembly, topology edge counting, per-tenant
cost rollups, append-only tenant-filtered audit, and the HTTP surface.
