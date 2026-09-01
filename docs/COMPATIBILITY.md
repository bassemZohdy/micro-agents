# Compatibility, Upgrades, and Deprecation Policy

This document defines what users may rely on across releases, how breaking
changes are announced, and how deprecated surfaces are retired. It is the
authoritative answer to the P2 policy items and applies from the current
release onward.

## Versioning

The project follows SemVer with a pre-1.0 caveat:

- **Patch (0.x.Z)** — bug fixes only. No API, schema, or behavior contract
  changes.
- **Minor (0.X.0)** — new features and deprecations. Breaking changes to
  **unstable** surfaces (see tiers) may land here with a deprecation notice
  in the prior release where feasible.
- **Major (X.0.0)** — breaking changes to **stable** surfaces.

Until 1.0, the minor version is the breaking-change channel; the release
gate (see TODO.md) keeps 1.0 claims off until P0/P1 acceptance criteria are
green.

## Stability tiers

| Surface | Tier | Contract |
|---|---|---|
| Definition schema (`microagents.io/v1alpha1`) | Stable (within alpha) | Additive fields only; removals require a new `apiVersion`; compatibility fixtures and migration notes ship in `docs/schemas` |
| HTTP API (`/v1/invoke`, health, capabilities, `/metrics`) | Stable | Response contracts (422/429/401/403/404/503/504) are additive; `/openapi.json` alias retained |
| Environment variables (`MICRO_AGENT_*`) | Stable | New variables are additive; renames are deprecations |
| Python core contracts (`micro_agent.core`, definition models) | Stable-ish | Additive; renames/removes follow the deprecation process |
| Python module layout (`micro_agent.observability` boundaries, `runtimes/*`) | Unstable | Internal reorganizations may occur in minors with changelog notes |
| Provider SPIs (`ModelProvider`, `SessionProvider`, `McpClient`, `Authenticator`, registries) | Unstable | Protocols evolve additively with default implementations where possible |

## Deprecation process

Every deprecation:

1. Is announced in `CHANGELOG.md` under `[Unreleased]` with the affected
   surface and the replacement.
2. Emits a runtime signal where one exists — `DeprecationWarning` for Python
   surfaces, a documented response header or field for HTTP.
3. Survives at least the remainder of the current minor release and one
   further minor release before removal (or removal at the next major,
   whichever gives longer notice).

Removals are listed in the changelog under **Removed**.

## Backward-compatibility re-exports (decision)

Today two import paths exist purely for compatibility:

- `micro_agent.observability` re-exports identity, policy, and side-effect
  types that live in `micro_agent.security` and `micro_agent.health`.
- `micro_agent.security.identity` re-exports `AgentIdentity` from
  `micro_agent.core`.

Decision: these re-exports are **supported import paths**, not accidental
leaks. When one must be retired:

1. The module grows a `__getattr__` shim that returns the re-exported object
   and emits a `DeprecationWarning` naming the canonical import path.
2. The warning ships for at least two minor releases.
3. Removal happens at the next major (or 1.0, whichever comes first).
4. The changelog records the removal under **Removed**.

No re-export is deprecated today; this decision fixes the mechanism so a
future cleanup cannot remove import paths silently.

## Upgrading

- Read the **Changed/Removed** sections of each release's changelog.
- For definitions: run the loader against your YAML; the strict Pydantic
  model reports every incompatibility with stable diagnostics, and schema
  fixtures under `docs/schemas` document the contract for each `apiVersion`.
- For deployments: `tools/validate_release.py` (release workflow) verifies
  schema version, image tag, package version, and changelog alignment; the
  manifest guard tests keep the Kubernetes samples honest.
