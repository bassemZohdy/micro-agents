# ADR 0005 — Deterministic Policy Enforcement in the Runtime

Date: 2026-08-30 · Status: Accepted in principle; implementation incomplete

## Context

Prompt instructions cannot be trusted to enforce autonomy boundaries; a
compromised or injected prompt must not override platform policy.

## Decision

`PolicyEvaluator` runs inside the runtime, not in the prompt: denied tools
and side effects are refused before execution (result surfaces as a tool
error), denied MCP servers fail agent startup, and denials are logged and
counted. Idempotency keys on tool arguments are recognized through an
`OperationRegistry` seam.

The current executable does not resolve definition `policy_refs` or
`credential_refs`, authenticate HTTP callers, enforce skills or model
restrictions, or provide an approval continuation. The operation registry is
in-memory and non-atomic.

## Consequences

- Enforcement survives prompt injection by construction.
- Programmatically injected policy is enforced at selected tool/MCP call sites.
- Platform policy resolution, verified identity propagation, durable audit,
  approval, and distributed idempotency remain release-blocking work.
