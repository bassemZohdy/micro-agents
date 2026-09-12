# ADR 0005 — Deterministic Policy Enforcement in the Runtime

Date: 2026-08-30 · Status: Accepted

## Context

Prompt instructions cannot be trusted to enforce autonomy boundaries; a
compromised or injected prompt must not override platform policy.

## Decision

`PolicyEvaluator` runs inside the runtime, not in the prompt: denied tools
and side effects are refused before execution (result surfaces as a tool
error), denied MCP servers fail agent startup, and denials are logged and
counted. Idempotency keys on tool arguments are recognized through an
`OperationRegistry` seam.

The executable resolves definition `policy_refs` and `credential_refs` through
configured providers, authenticates HTTP callers when enabled, enforces skill
and model restrictions, and supports approval continuations. The default
operation registry is in-memory; optional Redis and PostgreSQL registries add
atomic cross-process claims and result replay.

## Consequences

- Enforcement survives prompt injection by construction.
- Programmatically injected policy is enforced at selected tool/MCP call sites.
- External policy stores and downstream token delegation remain open
  production-hardening work. The reference runtime now also offers a retained,
  tenant-scoped SQLite audit sink; shared audit delivery and SIEM export remain
  deployment decisions. All implemented controls stay outside the prompt and
  are covered by contract tests.
