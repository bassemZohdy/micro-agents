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

## Standalone framework backlog

Remaining standalone production-readiness work, grouped by area. Each item
references its source in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
or the relevant ADR.

### P1 — Test coverage

- [x] Add unit tests for `micro_agent/session/sqlite.py` — direct coverage now
      exercises concurrent creation, close idempotence, tenant isolation, and
      optimistic version conflicts in `tests/test_session.py`.
- [x] Add unit tests for `micro_agent/__main__.py` — `parse_args`, `run`, and
      `main` are covered in `tests/test_main.py`.
- [x] Add dedicated unit tests for `micro_agent/security/approvals.py` —
      `InMemoryApprovalStore` save/get/delete and TTL behavior are covered in
      `tests/test_approvals.py`.
- [x] Add dedicated unit tests for `micro_agent/security/credentials.py` —
      environment and static providers are covered in `tests/test_credentials.py`.
- [x] Add tests for `micro_agent/tools/plugin.py` — class factories, callable
      factories, collision behavior, and invalid plugins are covered in
      `tests/test_examples.py`.
- [x] Add tests for `micro_agent/interoperability/a2a_server.py` — payload
      mapping and success/failure/cancellation task transitions are covered in
      `tests/test_a2a_server.py`.
- [ ] Expand Google ADK adapter tests (`runtimes/google_adk/runtime.py`,
      1100 lines, 15 tests): timeout handling, `stop()`/`shutdown()`
      lifecycle, health probes for model/memory/knowledge, session reuse,
      tool argument validation, model failure mid-invocation, and helper
      functions (`_adk_name`, `_messages_from_adk`, `_tools_from_adk`,
      `_event_text`, `_entry_text`, `_has_pending_confirmation`,
      `_approval_metadata`).
- [ ] Expand custom runtime tests (`runtimes/adk/runtime.py`, 1500 lines,
      6 tests): tool calls, policy enforcement, approval flow, retry/error
      policy, circuit breaker, knowledge retrieval, memory auto-store,
      checkpointing, streaming, session management, max-iterations,
      deadline/timeout, tool argument validation, operation registry.
- [x] Add coverage threshold to `pyproject.toml` (`[tool.coverage.report]`
      with `fail_under = 80`) to guard against regressions.
- [x] Add missing submodules to the import smoke test (`test_imports.py`):
      `micro_agent.security.approvals`, `micro_agent.security.credentials`,
      `micro_agent.session.sqlite`, `micro_agent.interoperability.a2a_server`,
      `micro_agent.tools.plugin`.

### P1 — Runtime (Google ADK adapter)

- [ ] Advertise streaming in the Google ADK adapter when the underlying
      model provider supports it. Currently always reports `streaming: false`.
- [ ] Advertise structured output in the Google ADK adapter. Currently
      always reports `structured_output: false`.
- [ ] Advertise checkpointing in the Google ADK adapter. Currently does not
      support it.
- [ ] Implement distributed idempotency mapping for the Google ADK adapter.
      Currently rejects `MICRO_AGENT_IDEMPOTENCY_ENDPOINT`.
      See [ADR 0011](docs/adr/0011-redis-idempotency-registry.md).
- [ ] Implement external session state bindings for the Google ADK adapter.
      Currently rejects SQLite and remote session persistence.
- [ ] Implement model credential reference resolution for the Google ADK
      adapter. Currently fails on credential refs on the model dependency.
- [ ] Prove runtime portability: run the same definition through both the
      custom loop and the Google ADK adapter under shared contract tests
      (ADR 0001 consequence).

### P1 — A2A protocol

- [ ] Implement A2A streaming tasks. The card currently advertises streaming
      as unavailable.
      See [docs/API.md](docs/API.md).
- [ ] Implement A2A push notifications.
- [ ] Implement A2A extended authenticated cards.
- [ ] Add durable A2A task store. Currently in-memory only; production state
      arrives with external state providers.
- [ ] Wire A2A task cancellation to the runtime invocation cancellation path.
      Currently not connected.
- [ ] Validate full A2A v1.0.1 conformance with the official SDK (beyond the
      tested non-streaming subset).
      See [docs/STANDARDS.md](docs/STANDARDS.md).

### P1 — Security and policy

- [ ] Implement downstream token delegation (token exchange toward MCP
      servers). Currently the verified principal is observable to operations
      but not forwarded through per-protocol delegation.
- [ ] Evaluate generic `PolicyRule` conditions. The condition field on policy
      rules is not processed.
- [ ] Implement external policy-store integration. Policy references cannot
      resolve from external stores; the bootstrap only accepts an injected
      policy or a policy resolver callable.
- [ ] Add durable approval store. Currently process-local; production
      approval state needs an external backend.
      See [ADR 0011](docs/adr/0011-redis-idempotency-registry.md).
- [ ] Add database-backed audit sink. Currently persists to stdout or a local
      file only.
- [ ] Validate OpenShift arbitrary-UID compatibility: group-writable paths,
      no pinned runtime UID, restricted SCC testing.
      See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### P1 — State and knowledge

- [ ] Implement a production knowledge provider. Currently only an in-memory
      keyword retriever exists.
      See [ADR 0012](docs/adr/0012-versioned-tenant-state.md).
- [ ] Implement a durable knowledge backend with versioned, tenant-scoped
      records.

### P2 — MCP

- [ ] Surface MCP notifications as application-level events. Currently
      consumed by the SDK session but invisible to the application layer.
- [ ] Add remote production MCP load testing. Currently only loopback HTTP
      and local stdio servers are tested.
      See [docs/STANDARDS.md](docs/STANDARDS.md).

### P2 — Models and tools

- [ ] Add additional model provider adapters beyond fake and OpenAI-compatible
      chat completions (Anthropic, Google Gemini native, Azure OpenAI, etc.).
- [ ] Add bundled native tools beyond `echo`. Domain tools currently require
      installed plugins or programmatic injection.
- [ ] Add credential provider integrations beyond environment bindings and
      `StaticCredentialProvider` (HashiCorp Vault, AWS Secrets Manager, cloud
      KMS).

### P2 — Definition and configuration

- [ ] Design a versioned resource/catalog contract for model alias resolution.
      Currently `model_alias` and `model_provider_id` are separate fields
      with no catalog mechanism.
- [ ] Design `v1beta1` or `v1` API version: separate model, schema,
      compatibility fixture, migration policy, and versioned loader.
      Currently only `microagents.io/v1alpha1` is supported.

### P2 — HTTP and observability

- [ ] Implement response streaming for the Google ADK adapter and A2A
      transport. Currently only the built-in runtime supports it.
- [ ] Add latency histogram support. Current latency metrics are gauges
      (latest value), not histograms; percentile dashboards require a native
      OTel histogram exporter.
      See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
- [ ] Add built-in rate-limiter implementation. Currently only an injected
      hook; no built-in algorithm or local counter exists. Shared
      gateway or datastore implementation required for replica-wide limits.
      See [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- [ ] Define proxy policy for production deployments. The OpenAI-compatible
      client defaults to `trust_env=False`; deployments requiring a proxy
      must opt in through provider configuration.

### P2 — Deployment hardening

- [ ] Generate hermetic hash-pinned `requirements.txt` for reproducible
      container builds. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- [ ] Define shutdown deadline and cancellation policy for requests that do
      not drain in time.
- [ ] Configure request-body limit and deadline budget enforcement at the
      ingress/gateway layer.
- [ ] Validate arbitrary-UID and read-only-filesystem execution in production
      images.
- [ ] Define resource requests/limits, disruption budgets, autoscaling,
      topology spread, and NetworkPolicy for production deployments.
- [ ] Scrape `/metrics` and define deployment-owned latency/error/readiness/
      token/cost dashboards and alerts.
      See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
- [ ] Perform rollback and compatibility-tested release validation.
- [ ] Validate immutable image, SBOM, signature, and SLSA provenance in
      production deployments.

### P2 — Benchmarks

- [ ] Add live-model/network/tool latency benchmarks. Current benchmarks
      intentionally exclude these and measure framework-overhead guardrails
      only, not production SLOs.
- [ ] Add distributed contention and production capacity-planning scenarios.

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
when the registry is down. 15 tests; `cloud` joined the strict mypy gate.
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

### C5 — Cloud hardening (deferred)

- [ ] Add durable persistence for the cloud registry (replace in-memory
      store). See [ADR 0014](docs/adr/0014-minimal-cloud-registry.md).
- [ ] Add durable persistence for the cloud config plane (replace in-memory
      store). See [ADR 0015](docs/adr/0015-versioned-cloud-config-plane.md).
- [ ] Add durable persistence for the cloud observability plane with
      retention/eviction policy. See [ADR 0017](docs/adr/0017-observability-aggregation.md).
- [ ] Add shared-state backends for gateway circuit-breaker, rate-limit, and
      bulkhead state (multi-replica production). See
      [docs/CLOUD_GATEWAY.md](docs/CLOUD_GATEWAY.md).
- [ ] Implement gateway streaming pass-through (currently buffers entire
      request/response bodies at 10 MB max). See
      [docs/CLOUD_GATEWAY.md](docs/CLOUD_GATEWAY.md).
- [ ] Add OIDC-backed gateway authenticator (replace static bearer tokens).
      See [ADR 0016](docs/adr/0016-gateway-edge-policy.md).
- [ ] Add Vault and cloud-managed secret-store resolvers (replace
      environment-only `SecretResolver`). See
      [cloud/config.py](cloud/config.py).
- [ ] Authenticate cloud plane APIs (registry, config, observability).
      Currently all planes are unauthenticated.
- [ ] Add formal schema and compatibility policy for cloud descriptors,
      config plane, and observability aggregation schemas.
- [x] Re-export C2/C3/C4 public symbols from `cloud/__init__.py`, with a
      top-level API contract test in `tests/test_cloud_api.py`.
- [ ] Evaluate splitting `cloud` into its own repository/deployment package.
      See [ADR 0014](docs/adr/0014-minimal-cloud-registry.md).

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
