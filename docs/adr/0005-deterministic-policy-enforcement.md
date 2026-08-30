# ADR 0005 — Deterministic Policy Enforcement in the Runtime

Date: 2026-08-30 · Status: Accepted

## Context

Prompt instructions cannot be trusted to enforce autonomy boundaries; a
compromised or injected prompt must not override platform policy.

## Decision

`PolicyEvaluator` runs inside the ADK runtime, not in the prompt: denied tools
and side effects are refused before execution (result surfaces as a tool
error), denied MCP servers fail agent startup, and denials are logged and
counted. Idempotency keys on tool arguments are honored through
`OperationRegistry` deduplication.

## Consequences

- Enforcement survives prompt injection by construction.
- Policy is configuration (`AdkRuntimeConfig.policy`), resolved from platform
  configuration rather than agent-authored content.
