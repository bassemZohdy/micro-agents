# ADR 0002 — One Runtime First, with a Fake Model for CI

Date: 2026-08-30 · Status: Accepted in principle; implementation incomplete

## Context

The framework needs one concrete runtime first, and CI must run without paid
model access.

## Decision

Maintain one reference runtime behind the small `AgentRuntime` SPI and a
deterministic `FakeModelProvider` for tests. Production startup must require an
explicit provider selection and must never silently fall back to the fake.

Google ADK remains the intended first external framework adapter, but the
current `runtimes/adk` implementation is a project-owned model/tool loop. It
has no Google ADK dependency or ADK-native lifecycle. `OpenAICompatProvider`
can be injected through Python, but the command-line bootstrap does not select
it from resolved configuration.

## Consequences

- CI needs no network or API keys; behavioral tests cover the full invoke loop.
- The current package must be renamed to describe the custom loop or replaced
  by a genuine ADK adapter.
- Provider selection, credential resolution, and explicit fake/development
  mode are release-blocking bootstrap work.
