# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); implementation evidence and limitations belong
in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Backlog audited against `main` at commit `cdedc00` on 2026-09-12.

## Release gate

Until the standalone release gate is complete:

- do not describe the framework as production-ready or publish a stable
  release;
- treat the Cloud C0–C4 code as minimal reference/control-plane slices, not as
  a production cloud offering;
- defer Cloud C5 production hardening and expansion.

### P0 — Release correctness

- [ ] Configure and verify a pending PyPI trusted publisher on pypi.org for
      project `micro-agents`, owner `bassemZohdy`, repository `micro-agents`,
      workflow filename `release.yml`, and an **empty environment name**. The
      publish job already declares `id-token: write` and uses
      `pypa/gh-action-pypi-publish@release/v1`; the remaining action is on
      pypi.org (Manage → Publishing). Afterward, cut the first release with
      `git tag v0.1.0 && git push origin v0.1.0` as documented in
      [DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Next tasks

The immediate release task is the owner-side PyPI trusted-publisher setup
above. The next implementation task is a shared multi-replica A2A/state
backend and full protocol conformance. Cloud C5 hardening remains gated until
the release task is complete.

## Standalone framework backlog

### P1 — A2A and production state

- [x] Add a durable, tenant-scoped A2A task store behind an SPI. The SQLite
      reference store persists bounded task snapshots (including status,
      context, artifacts, and cancellation transitions), expiry, and task-ID
      lookup; a shared multi-replica backend remains open.
- [x] Implement A2A push notifications on top of the durable store, including
      callback registration, bounded transient retry, HTTPS/host validation,
      callback authentication, and card capability advertisement. Delivery
      expiry and shared-worker cancellation policy remain conformance work.
- [x] Implement authenticated A2A card declarations and test their security
      scheme against the configured authenticator. Extended card fields beyond
      the supported SDK model remain part of conformance validation.
- [ ] Validate full A2A v1.0.1 conformance with the official SDK, including
      unsupported/error paths beyond the currently tested non-streaming and
      streaming subset.
- [ ] Implement a production semantic knowledge provider. The durable,
      versioned, tenant-scoped SQLite keyword backend is complete as a
      portable reference but is not a distributed vector/search service.

### P1 — Security and policy

- [ ] Implement downstream token delegation/token exchange for protocol
      clients such as MCP servers. Verified identity propagation exists, but
      per-protocol delegated credentials do not.
- [ ] Implement external policy-store integration so declared policy
      references can resolve from a configured service rather than only an
      injected policy or resolver callable.
- [x] Add a database-backed SQLite audit sink with retention and tenant
      scoping. Shared-database delivery, failure policy, and SIEM export remain
      deployment decisions.
- [ ] Validate OpenShift arbitrary-UID and read-only-filesystem execution
      under the target restricted security context constraints.

### P2 — MCP

- [x] Surface MCP notifications as bounded application-level events through
      the connection-manager callback and snapshot API.
- [ ] Add remote production MCP load testing beyond loopback HTTP and local
      stdio servers.

### P2 — Models, tools, and credentials

- [ ] Add model adapters beyond the fake and OpenAI-compatible chat-completion
      providers, such as Anthropic, native Google Gemini, or Azure OpenAI.
- [ ] Add bundled native tools beyond `echo`; domain tools currently require
      installed plugins or programmatic injection.
- [ ] Add credential integrations beyond environment bindings and
      `StaticCredentialProvider`, such as Vault, AWS Secrets Manager, or a
      cloud KMS.

### P2 — Definition and configuration

- [ ] Design a versioned resource/catalog contract for model alias
      resolution. `model_alias` and `model_provider_id` are currently separate
      fields without a catalog mechanism.
- [x] Add a `v1beta1` compatibility version with generated schema, fixture,
      camelCase migration policy, and versioned loader. It reuses the strict
      runtime model behind an explicit migration boundary; a fully separate
      model is deferred until the schema diverges.

### P2 — HTTP and observability

- [x] Add latency histogram support to the built-in metrics collector. OTel
      histogram export remains opt-in through the configured SDK provider.
- [x] Add a documented bounded process-local token-bucket rate limiter with an
      injectable store and shared-state SPI. A distributed deployment still
      needs a gateway/datastore implementation.
- [x] Define and test the outbound proxy policy: model and MCP clients default
      to `trust_env=False`; proxy use requires explicit client/provider
      configuration.

### P2 — Deployment hardening

- [ ] Generate a hermetic, hash-pinned `requirements.txt` for reproducible
      container builds; see [DEPLOYMENT.md](docs/DEPLOYMENT.md).
- [ ] Define and validate the deployment shutdown deadline and cancellation
      policy for requests that do not drain in time.
- [x] Enforce application request-body limits and deadline budgets, including
      chunked requests. The ingress/gateway must apply a matching limit before
      the application for complete edge protection.
- [ ] Define production resource requests/limits, disruption budgets,
      autoscaling, topology spread, and NetworkPolicy decisions.
- [ ] Scrape `/metrics` and define deployment-owned latency, error, readiness,
      token, and cost dashboards and alerts.
- [ ] Perform rollback and compatibility-tested release validation.
- [ ] Validate immutable image references, SBOM, signatures, and SLSA
      provenance in production deployment policy.

### P2 — Benchmarks

- [ ] Add live-model, network, and tool latency benchmarks. Existing scenarios
      intentionally measure framework overhead with the fake provider.
- [ ] Add distributed contention and production capacity-planning scenarios.

## Micro-Agent Cloud — C5 production hardening

Cloud C0–C4 reference slices are implemented in the top-level `cloud` package;
the following work remains deferred until the standalone release gate closes.

- [ ] Add durable persistence for the cloud registry, with retention and
      lease-recovery behavior. See [ADR 0014](docs/adr/0014-minimal-cloud-registry.md).
- [ ] Add durable persistence for the cloud config plane. See
      [ADR 0015](docs/adr/0015-versioned-cloud-config-plane.md).
- [ ] Add durable persistence for cloud observability with retention and
      eviction policy. See [ADR 0017](docs/adr/0017-observability-aggregation.md).
- [ ] Add shared-state backends for gateway circuit-breaker, rate-limit, and
      bulkhead state across replicas. See [CLOUD_GATEWAY.md](docs/CLOUD_GATEWAY.md).
- [ ] Implement gateway streaming pass-through; the current gateway buffers
      request and response bodies with a 10 MB limit.
- [ ] Add an OIDC-backed gateway authenticator to replace static bearer tokens.
- [ ] Add Vault and cloud-managed secret-store resolvers.
- [ ] Authenticate the registry, config, and observability plane APIs.
- [ ] Define formal schemas and compatibility policy for cloud descriptors,
      config-plane payloads, and observability events.
- [ ] Evaluate splitting `cloud` into its own repository and deployment
      package after the standalone contracts stabilize.

## Deferred / non-goals

- LangChain or another second runtime
- visual designer
- workflow engine
- agent marketplace
- distributed memory platform
