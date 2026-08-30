# ADR 0002 — One Runtime First, with a Fake Model for CI

Date: 2026-08-30 · Status: Accepted in principle; implementation incomplete

## Context

The framework needs one concrete runtime first, and CI must run without paid
model access.

## Decision

Maintain one reference runtime behind the small `AgentRuntime` SPI and a
deterministic `FakeModelProvider` for tests. Production startup must require an
explicit provider selection and must never silently fall back to the fake.

Google ADK remains the first external framework adapter. The project-owned
loop stays in `runtimes/adk`, while `runtimes/google_adk` provides the genuine
adapter behind the same SPI. The `google-adk` package is an optional pinned
extra so fake-provider CI remains deterministic. The executable bootstrap
still selects the custom loop by default; adapter selection and additional
provider mappings remain open work.

## Consequences

- CI needs no network or API keys; behavioral tests cover the full invoke loop.
- The custom loop and genuine adapter have separate package boundaries.
- Credential providers beyond environment bindings and a genuine ADK adapter
  remain release-blocking work.
