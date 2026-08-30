# ADR 0007 — Stable Protocol Baselines

Date: 2026-08-30 · Status: Accepted

## Context

Claims of A2A or MCP compatibility need an exact, stable specification and an
independent interoperability test. Project-local data classes and fake clients
do not establish protocol compliance.

## Decision

- Target A2A v1.0.1 for agent discovery and task/message interoperability.
- Target the MCP `2025-11-25` stable specification.
- Prefer official Python SDK types and conformance clients at protocol
  boundaries while keeping SDK types behind runtime-neutral project APIs.
- Treat release candidates or draft protocol revisions as opt-in experiments,
  never as the default compatibility claim.

## Consequences

- Cards, routes, transports, version negotiation, and task/tool lifecycles are
  tested with clients not authored against project-specific expectations.
- Compatibility statements name the version and binding they cover.
- Upgrades require fixtures, migration notes, and interoperability evidence.
