# 0009 — Compatibility policy and re-export deprecation mechanism

Status: Accepted

## Context

The framework grew surfaces that callers import or depend on in two ways:
the intended public surface (definition schema, HTTP API, environment
variables, core contracts) and convenience re-exports added during
development (`micro_agent.observability` re-exporting security and health
types; `micro_agent.security.identity` re-exporting `AgentIdentity`).
Pre-1.0 refactors need a truthful, written policy for what may break and
how deprecations are announced, so removals are never silent.

## Decision

1. Adopt the tiered stability policy in `docs/COMPATIBILITY.md`: definition
   schema, HTTP API, and environment variables are stable-within-alpha;
   Python module layout and provider SPIs are unstable but deprecation-
   announced.
2. Deprecations require a changelog announcement, a runtime signal where a
   surface supports one, and a grace window of the current minor plus one
   further minor (or the next major).
3. Backward-compatibility re-exports are supported paths. Retirement uses a
   module `__getattr__` shim emitting `DeprecationWarning` with the
   canonical path, held for two minors, removed at the next major/1.0.

## Consequences

- Refactors of module layout remain possible in minors but must be
  announced and warned; silent import-path removal is a policy violation.
- The changelog gains a de-facto **Removed** section contract.
- Adding a new re-export requires deciding its canonical home first and
  documenting the re-export in the module docstring.
