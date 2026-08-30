# ADR 0008 — Runtime Adapter Truthfulness

Date: 2026-08-30 · Status: Proposed

## Context

The package `runtimes/adk` is currently a project-owned asynchronous
model/tool loop. It does not depend on or invoke Google ADK. Package names and
documentation that imply otherwise make capability and support claims that
the implementation cannot demonstrate.

## Proposed decision

Only name an adapter after an external runtime when it constructs and invokes
that runtime's supported APIs and passes adapter-specific integration tests.
Either:

1. rename the existing code as a built-in/reference loop and add a separate
   Google ADK adapter; or
2. replace the package internals with Google ADK while retaining the
   runtime-neutral `AgentRuntime` boundary.

## Consequences

- Documentation and capability reporting remain verifiable.
- The deterministic custom loop may remain valuable for tests, examples, or a
  lightweight runtime, but it is not evidence of ADK support.
- The final option requires a compatibility and migration review before this
  ADR becomes accepted.
