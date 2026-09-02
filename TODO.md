# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); evidence and limitations belong in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Baseline audited: `755cf68` on 2026-09-02.

## Release gate

Do not describe the framework as production-ready, publish a stable release,
or start Micro-Agent Cloud implementation until both standalone release
blockers are complete and the relevant acceptance tests are green.

### P0 — Release correctness

- [ ] Protect `main` and release tags with the required CI checks in GitHub
      rulesets; workflow gates alone do not prevent an administrator bypass.
- [ ] Configure and verify a pending PyPI trusted publisher for project
      `micro-agents`, owner `bassemZohdy`, repository `micro-agents`, and
      workflow `release.yml` before creating the first release tag.

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
