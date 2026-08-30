# ADR 0001 — Runtime-Neutral Micro-Agent Definition

Date: 2026-08-30 · Status: Accepted

## Context

Agents must be describable independently of any agent framework so the same
artifact can run on different runtimes and in different environments.

## Decision

The Micro-Agent Definition (`apiVersion: microagents.io/v1alpha1`) is defined
by pydantic models in `micro_agent/definition/models.py` with camelCase JSON
aliases (`apiVersion`), a generated JSON Schema (`by_alias=True`, kept in
`docs/schemas/` with a CI drift check), and YAML examples. No framework-native
types appear in definition or core contracts.

## Consequences

- Definitions are portable and externally validatable; the published schema
  matches the loader exactly.
- Runtime-specific concepts (ADK agents, tools) are bound at runtime
  construction, never in the definition.
