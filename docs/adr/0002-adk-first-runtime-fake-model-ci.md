# ADR 0002 — ADK-First Runtime with a Fake Model for CI

Date: 2026-08-30 · Status: Accepted

## Context

The framework needs one concrete runtime first, and CI must run without paid
model access.

## Decision

`runtimes/adk` is the reference runtime behind the small `AgentRuntime` SPI.
The default model provider is a deterministic `FakeModelProvider`; a real
provider is selected by configuration via `OpenAICompatProvider` (plain HTTP
against any /chat/completions endpoint), avoiding a hard vendor-SDK dependency
in the core.

## Consequences

- CI needs no network or API keys; behavioral tests cover the full invoke loop.
- Real deployments configure endpoint + credential reference via environment;
  the fake provider never silently serves production (start() health-checks
  the configured provider).
