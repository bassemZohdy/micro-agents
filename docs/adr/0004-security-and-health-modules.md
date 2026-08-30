# ADR 0004 — Security and Health Modules outside Observability

Date: 2026-08-30 · Status: Accepted

## Context

Identity, policy, side effects, and health checks were initially placed under
`micro_agent/observability/` although they are not observability concerns.

## Decision

Move them: `micro_agent/security/` (identity, policy, side effects, security
context) and `micro_agent/health/` (liveness/readiness/probes).
`micro_agent/observability` keeps telemetry and re-exports the moved names for
backward compatibility.

## Consequences

- Import sites stay stable during migration; new code imports from
  `micro_agent.security` / `micro_agent.health`.
