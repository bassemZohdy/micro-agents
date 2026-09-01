# ADR 0003 — Structured Telemetry Facade with Optional OpenTelemetry

Date: 2026-08-30 · Status: Accepted (updated 2026-09-01)

## Context

The invocation path needs correlated logs, metrics, and spans while keeping the
base package lightweight and deterministic in tests. Deployments that need
standard telemetry must be able to opt in without leaking OpenTelemetry types
through the runtime SPI.

## Decision

`micro_agent.observability.Telemetry` remains the single facade. It always
provides structured logging, in-memory metrics, and an in-memory span tree for
tests; the optional `otel` extra adds SDK-backed spans/metrics and W3C
propagation behind the same call sites. Content attributes are disabled by
default and metric labels are bounded.

## Consequences

- Agent/model/tool/MCP spans share the facade's correlation; OTel-enabled
  deployments export standard spans/metrics without changing runtime code.
- Secret redaction is centralized in the logger.
- HTTP middleware and outbound model/MCP clients extract and inject W3C trace
  context when OTel is enabled; token/cost conventions and `/metrics` expose
  the operational contract, while production dashboards and alerts remain
  deployment-owned.
