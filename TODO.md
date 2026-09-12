# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); implementation evidence and limitations belong
in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Backlog audited against merged `main` on 2026-09-12.

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
above. Implementation work now focuses on distributed contention/capacity
validation plus production-cluster admission and promotion checks. SQLite and
Redis cover the tested reference state paths;
shared database service operations and deployment-environment validation remain
separate work.

## Standalone framework backlog

### P1 — A2A and production state

- [x] Add a durable, tenant-scoped A2A task store behind an SPI. SQLite is the
      portable reference and Redis provides bounded, TTL-backed task snapshots
      for independently scaled workers; shared Redis operations and failover
      still require deployment validation.
- [x] Implement A2A push notifications on top of the durable stores, including
      callback registration, bounded transient retry, HTTPS/host validation,
      callback authentication, and card capability advertisement. Delivery
      expiry and shared-worker cancellation policy remain conformance work.
- [x] Implement authenticated A2A card declarations and test their security
      scheme against the configured authenticator. Extended card fields beyond
      the supported SDK model remain part of conformance validation.
- [x] Validate the supported A2A v1.0.1 surface with the official SDK,
      including card discovery, non-streaming/streaming tasks, cancellation,
      task get/list, push-configuration CRUD, not-found errors, and protocol
      version rejection. Optional extended-card features remain unadvertised
      until their deployment contract is defined.
- [x] Implement a production-facing semantic knowledge provider adapter. The
      bounded `HttpKnowledgeRetriever` supports HTTPS semantic/vector or hybrid
      search services with tenant/version propagation, strict result parsing,
      optional bearer auth, content hashes, and readiness probing. Operating a
      distributed vector/index service and validating its capacity remain
      deployment work; SQLite remains the portable reference backend.

### P1 — Security and policy

- [x] Implement downstream token delegation/token exchange for remote MCP
      servers. The optional strict HTTPS exchange provider receives verified
      invocation identity, issues a short-lived Bearer token, and refreshes it
      for each remote MCP JSON-RPC request; stdio credentials remain startup
      scoped because child-process environments cannot rotate per call.
- [x] Implement external policy-store integration so declared policy
      references resolve from a configured HTTPS service through a strict,
      bearer-authenticated response contract; injected policies and resolver
      callables remain supported.
- [x] Add a database-backed SQLite audit sink with retention and tenant
      scoping. Shared-database delivery, failure policy, and SIEM export remain
      deployment decisions.
- [x] Validate the image's arbitrary-UID and read-only-filesystem execution in
      CI with UID `12345:0`, a read-only root, and a writable `/tmp` tmpfs;
      target-cluster OpenShift SecurityContextConstraints remain a deployment
      review item.

### P2 — MCP

- [x] Surface MCP notifications as bounded application-level events through
      the connection-manager callback and snapshot API.
- [x] Add an operator-invoked remote benchmark harness for deployed Micro-Agent
      HTTPS endpoints and Streamable HTTP MCP servers. It uses bounded
      concurrency, reports latency/error/throughput metrics, and keeps bearer
      tokens out of arguments and reports; live execution and SLO review remain
      deployment-owned.

### P2 — Models, tools, and credentials

- [x] Add a model adapter beyond the fake and OpenAI-compatible providers:
      the native Anthropic Messages adapter supports tool-use translation and
      streaming; native Google Gemini and Azure OpenAI remain optional future
      adapters.
- [x] Add a bundled native tool beyond `echo`: the bounded, side-effect-free
      `json_parse` utility is available for portable structured-data flows;
      domain tools still require installed plugins or programmatic injection.
- [x] Add Vault KV v2 and AWS Secrets Manager credential integrations beyond
      environment bindings and `StaticCredentialProvider`. Vault resolves
      `vault://mount/path#field`; AWS resolves
      `aws-secretsmanager://secret-id[#json-field]`; both use fresh lookups and
      strict response validation. Cloud KMS remains a deployment-owned adapter.

### P2 — Definition and configuration

- [x] Design and implement a versioned model-alias catalog contract. The
      injected and HTTP catalog implementations resolve `model.ref` to
      provider/model/endpoint metadata with strict version and alias checks;
      broader resource catalogs remain future work.
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

- [x] Generate a hermetic, hash-pinned Linux/Python 3.11 runtime
      `requirements.txt` for reproducible container builds; the Dockerfile
      installs it with `pip --require-hashes`. Regenerate only on runtime
      dependency-bound changes; see [DEPLOYMENT.md](docs/DEPLOYMENT.md).
- [x] Define and validate the deployment shutdown deadline and cancellation
      policy: the sample definition drains for 25 seconds, cancels remaining
      invocations, and the Kubernetes Deployment grants a 30-second grace
      period.
- [x] Enforce application request-body limits and deadline budgets, including
      chunked requests. The ingress/gateway must apply a matching limit before
      the application for complete edge protection.
- [x] Define the checked-in production baseline for resource requests/limits,
      disruption budgets, autoscaling, topology spread, and NetworkPolicy;
      provider-specific selectors remain a cluster review item.
- [x] Add Service scrape metadata and define latency, error, readiness, token,
      and cost dashboards and alerts in the observability documentation.
- [x] Perform compatibility-tested release validation: the tag gate loads both
      supported definition fixtures and verifies append-only config rollback,
      alongside package/schema/image/changelog alignment. Live promotion and
      production rollback execution remain deployment-environment work.
- [x] Add release-CI validation for immutable image digests, SBOM
      attestation, keyless Cosign signatures, and SLSA provenance. The exact
      published digest is now signed and verified before the GitHub release.
- [ ] Enforce digest, Cosign identity, SBOM, and SLSA admission policy in the
      target production cluster and validate live promotion.

### P2 — Benchmarks

- [x] Add an external benchmark harness for live-model, network, and tool
      latency through deployed HTTP/MCP endpoints. Existing CI remains fake and
      deterministic; production baselines remain deployment-owned.
- [ ] Add distributed contention and production capacity-planning scenarios.

## Micro-Agent Cloud — C5 production hardening

Cloud C0–C4 reference slices and SQLite durability reference backends are
implemented in the top-level `cloud` package; the following work remains
deferred until the standalone release gate closes.

- [x] Add durable persistence for the cloud registry, with stale-retention and
      restart-safe lease recovery in `SqliteAgentRegistry`. Shared database
      operations remain deployment work. See [ADR 0014](docs/adr/0014-minimal-cloud-registry.md).
- [x] Add durable persistence for the cloud config plane, with transactional
      versions and optional retention in `SqliteConfigStore`. Shared database
      operations remain deployment work. See [ADR 0015](docs/adr/0015-versioned-cloud-config-plane.md).
- [x] Add durable persistence for cloud observability with retention and
      bounded eviction in `SqliteObservabilityStore`. Shared ingestion and
      plane authentication remain deployment work. See [ADR 0017](docs/adr/0017-observability-aggregation.md).
- [x] Add a shared Redis backend for gateway circuit-breaker, rate-limit, and
      bulkhead state across replicas. Atomic scripts provide tenant/route rate
      buckets, per-target breaker probes, and TTL-backed bulkhead leases; Redis
      availability, sizing, and failover remain deployment validation. See
      [CLOUD_GATEWAY.md](docs/CLOUD_GATEWAY.md).
- [x] Implement gateway event-stream response pass-through. Requests remain
      bounded before forwarding (10 MB); accepted `text/event-stream` responses
      are streamed from the upstream and release the target bulkhead slot when
      the stream completes or disconnects.
- [x] Add an OIDC-backed gateway authenticator to replace static bearer tokens;
      the gateway now validates issuer/audience/expiry/signature and maps the
      verified tenant claim, while static tokens remain available for local use.
- [x] Add Vault KV v2 and AWS Secrets Manager resolvers; cloud KMS and
      deployment-specific secret bindings remain deployment-owned adapters
      behind the credential-provider interface.
- [x] Add explicit authentication middleware to the registry, config, and
      observability plane APIs. Static-token and OIDC gateway authenticators
      can be supplied at app construction; readiness remains public and the
      reference apps retain an unauthenticated local default.
- [x] Define formal schemas and compatibility policy for cloud descriptors,
      config-record payloads, and observability events. Generated v1alpha1
      artifacts are checked in under `docs/schemas/` and the registry/config/
      observability boundaries validate the same contracts.
- [ ] Evaluate splitting `cloud` into its own repository and deployment
      package after the standalone contracts stabilize.

## Deferred / non-goals

- LangChain or another second runtime
- visual designer
- workflow engine
- agent marketplace
- distributed memory platform
