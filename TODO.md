# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); evidence and limitations belong in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Baseline audited: `d6e14f7` on 2026-09-03.

## Release gate

Do not describe the framework as production-ready, publish a stable release,
or start Micro-Agent Cloud implementation until both standalone release
blockers are complete and the relevant acceptance tests are green.

### P0 — Release correctness

- [x] Protect `main` and release tags with the required CI checks in GitHub
      rulesets; workflow gates alone do not prevent an administrator bypass.
      Done 2026-09-03 via two active rulesets with an empty bypass list:
      `main-required-CI` (deletion and force-push blocked, all 15 PR-visible
      CI checks required) and `release-tags-immutable` (release tags `v*`
      cannot be deleted or moved). Updates to `main` now go through pull
      requests so the checks can pass before the ref advances.
- [ ] Configure and verify a pending PyPI trusted publisher on pypi.org for
      project `micro-agents`, owner `bassemZohdy`, repository `micro-agents`,
      workflow filename `release.yml`, and an **empty environment name** (the
      publish job declares no GitHub environment) before creating the first
      release tag. Owner action on pypi.org (Manage → Publishing); the
      workflow side is already ready (`id-token: write` +
      `pypa/gh-action-pypi-publish@release/v1`). Everything else is cut-ready:
      the changelog `[Unreleased]` notes are folded into `[0.1.0]`, the
      package version and deployment image pin already say `0.1.0`, and
      `tools/validate_release.py 0.1.0` passes — once the pending publisher
      exists, the release is `git tag v0.1.0 && git push origin v0.1.0`
      (see "Cutting a release" in docs/DEPLOYMENT.md).

## Micro-Agent Cloud — gated future workstream

Start only after the standalone release gate is complete.

### C0 Architecture

- [x] Define boundaries between core framework and cloud services.
- [x] Distinguish semantic agent discovery from technical service discovery.
- [x] Define extension, tenancy, security, and failure models.

Defined 2026-09-03 in [docs/architecture/CLOUD_ARCHITECTURE.md](docs/architecture/CLOUD_ARCHITECTURE.md)
and [ADR 0013](docs/adr/0013-cloud-control-plane-boundary.md): cloud services
are external control-plane deployables (the core never imports cloud code and
serves with the control plane down), semantic descriptors derive from the
definition and served A2A agent card and are checked against it at
registration, tenancy follows the verified `tenant_id` claim at every plane,
and every cloud failure mode degrades to the standalone system. Design only —
C1+ implementation remains deferred until the release gate closes.

### C1 Registry and discovery

- [x] Define versioned agent/skill descriptors.
- [x] Build a minimal registry and health-aware discovery client.

Done 2026-09-03 in the top-level `cloud` package (core untouched, boundary
per ADR 0013; see [docs/CLOUD_REGISTRY.md](docs/CLOUD_REGISTRY.md)):
`v1alpha1` descriptors derived from the definition and served A2A agent card
(card contradictions are rejected at registration), a lease-based in-memory
registry with a FastAPI surface (register/heartbeat/deregister/query with
tenant and skill filters, stale-with-age entries, 422 identity checks), and
a discovery client that degrades to per-query cached snapshots marked stale
when the registry is down. 10 tests; `cloud` joined the strict mypy gate.
Deliberately deferred: registry authentication (C3 gateway) and persistence
(C2 config plane).

### C2 Distributed configuration

- [x] Store versioned definitions and environment overlays.
- [x] Integrate existing secret-management systems.

Done 2026-09-03 in the `cloud` package ([docs/CLOUD_CONFIG.md](docs/CLOUD_CONFIG.md),
ADR 0015): append-only per-agent version histories validated by the core's
own definition loader and `EnvironmentOverlay` model, rollbacks that append
rather than rewrite, and a `SecretResolver` protocol (environment resolver
included) so secret-management systems integrate at use time while the
plane stores references only. Durable backends replace the in-memory store
in a later slice; edge auth is C3.

### C3 Gateway and resilience

- [ ] Route A2A traffic with authentication, authorization, rate limits, and
      policy.
- [ ] Provide retries, circuit breakers, bulkheads, load balancing, and
      fallbacks at the appropriate layer.

### C4 Distributed observability

- [ ] Provide cross-agent tracing, cost/usage aggregation, topology, and audit
      views.

## Deferred

- LangChain or other second runtime
- visual designer
- workflow engine
- agent marketplace
- distributed memory platform
