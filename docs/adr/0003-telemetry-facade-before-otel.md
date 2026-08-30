# ADR 0003 — Structured Telemetry Facade before OpenTelemetry

Date: 2026-08-30 · Status: Accepted

## Context

The invocation path needs correlated logs, metrics, and spans now; full
OpenTelemetry integration (SDK, exporter, context propagation) is planned but
must not block a working runtime.

## Decision

`micro_agent.observability.Telemetry` is the single facade (StructuredLogger
with secret redaction, MetricsCollector, in-memory TraceSpan tree) wired into
the runtime and HTTP layer at fixed call sites.

## Consequences

- Agent/model/tool/MCP spans share trace IDs now; swapping the collector for
  an OTel exporter later changes only the facade, not call sites.
- Secret redaction is centralized in the logger.
- The facade is not itself OpenTelemetry-compatible evidence: it does not
  propagate W3C trace context or export standard spans and metrics.
