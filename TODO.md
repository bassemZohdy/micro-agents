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

- [ ] Define boundaries between core framework and cloud services.
- [ ] Distinguish semantic agent discovery from technical service discovery.
- [ ] Define extension, tenancy, security, and failure models.

### C1 Registry and discovery

- [ ] Define versioned agent/skill descriptors.
- [ ] Build a minimal registry and health-aware discovery client.

### C2 Distributed configuration

- [ ] Store versioned definitions and environment overlays.
- [ ] Integrate existing secret-management systems.

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
