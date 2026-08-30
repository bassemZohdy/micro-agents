# ADR 0008 — Runtime Adapter Truthfulness

Date: 2026-08-30 · Status: Accepted in part

## Context

The package `runtimes/adk` is currently a project-owned asynchronous
model/tool loop. It does not depend on or invoke Google ADK. Package names and
documentation that imply otherwise make capability and support claims that
the implementation cannot demonstrate.

## Decision

Only name an adapter after an external runtime when it constructs and invokes
that runtime's supported APIs and passes adapter-specific integration tests.
The repository uses the separate-adapter option:

1. `runtimes/adk` remains the built-in/reference loop.
2. `runtimes/google_adk` constructs and invokes Google ADK APIs behind the
   runtime-neutral `AgentRuntime` boundary.
3. The Google ADK dependency is optional and pinned; default fake-provider CI
   does not require credentials.

## Consequences

- Documentation and capability reporting remain verifiable.
- The deterministic custom loop may remain valuable for tests, examples, or a
  lightweight runtime, but it is not evidence of ADK support.
- Complete service mapping remains a compatibility and migration follow-up.
