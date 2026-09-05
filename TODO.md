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

- [x] Route A2A traffic with authentication, authorization, rate limits, and
      policy.
- [x] Provide retries, circuit breakers, bulkheads, load balancing, and
      fallbacks at the appropriate layer.

Done 2026-09-04 in the `cloud` package ([docs/CLOUD_GATEWAY.md](docs/CLOUD_GATEWAY.md),
ADR 0016): `/{agent}/...` routing through a pluggable edge authenticator
(static bearer tokens → tenant claims), per-route tenant authorization,
per-tenant token-bucket rate limits, and the full resilience set —
round-robin load balancing, ordered fallbacks, per-target circuit breakers
with half-open probes, per-target bulkheads that skip (never queue) when
saturated, and retries confined to safe or idempotency-keyed calls.
Agent-local enforcement is untouched; credentials are forwarded end to end.
Shared-state backends and streaming pass-through are later work.

### C4 Distributed observability

- [x] Provide cross-agent tracing, cost/usage aggregation, topology, and audit
      views.

Done 2026-09-04 in the `cloud` package
([docs/CLOUD_OBSERVABILITY.md](docs/CLOUD_OBSERVABILITY.md), ADR 0017):
batch event ingestion aggregated into cross-agent traces (via
`caller_agent` span attributes), a caller→callee topology view with call
counts, per-agent/tenant cost rollups, and an append-only tenant-filterable
audit view. Agents keep writing telemetry/audit locally and tamper-evident
at the source; the plane is read-mostly and losing it costs visibility,
never agents. In-memory store by design for the minimal slice; durable
backends replace it later.

## Cloud hardening backlog

Found during the 2026-09-04 full-project review. These are correctness and
hardening fixes to the already implemented minimal cloud slices, not expansion
beyond the documented C1-C4 scope.

- [x] Fix gateway routing so `GET /gateway/health` is registered before the
      `/{agent}/{path:path}` catch-all and is reachable. (PR: gateway hardening)
- [x] Forward inbound query strings through the gateway to upstream targets.
- [x] Make registration/heartbeat TTLs work end to end: send `ttl_seconds`
      from `RegistryDiscoveryClient`, accept it on the registry HTTP routes,
      and reject non-positive heartbeat TTLs just as registration does.
      (verified by tests/test_cloud_registry.py)
- [x] Align the registry query contract and implementation for result ordering
      — (name, version) ordering documented in docs/CLOUD_REGISTRY.md and pinned
      by an HTTP-layer test regardless of registration order.
- [x] Make observability batch ingest atomic and validate that every event is
      an object so malformed batches return 422 without partial writes or a
      500 (verified by tests/test_cloud_observability.py).
- [x] Return clean registry 404 details without `KeyError`'s embedded quotes
      (verified by tests/test_cloud_registry.py).
- [x] Treat client-side HTTP errors as authoritative in the config/discovery
      clients instead of misclassifying 4xx responses as plane outages or
      serving stale cache entries; reserve fallback for transport failures and
      5xx responses (verified by tests in test_cloud_config.py /
      test_cloud_registry.py).
- [x] Make failed non-retryable gateway calls return the executed target's
      response consistently, regardless of how many targets the route has.
- [x] Propagate safe upstream response headers through the gateway instead of
      dropping everything except the content type (hop-by-hop, content-length,
      content-encoding stripped).
- [x] Bound gateway rate-limit state with idle eviction and a maximum bucket
      count so distinct-token spraying cannot grow memory without limit
      (`rate_limit_max_buckets`, `rate_limit_idle_seconds`, `rate_limit_bucket_count()`).
- [x] Validate observability audit-read limits at the HTTP boundary
      (verified by tests/test_cloud_observability.py).
- [x] Use constant-time bearer-token comparison in the gateway's static
      development authenticator (`hmac.compare_digest`).
- [x] Include `cloud` in the release workflow's strict mypy command so the
      release gate is at least as strong as CI.

## Deferred

- LangChain or other second runtime
- visual designer
- workflow engine
- agent marketplace
- distributed memory platform
- Durable, shared cloud backends for registry, configuration, gateway state,
  and observability; all four planes currently document in-memory state as a
  minimal-slice limitation.
- Authentication for the standalone registry, config, and observability plane
  apps; they are currently documented as unauthenticated behind the C3 edge.
- Streaming pass-through at the gateway.
