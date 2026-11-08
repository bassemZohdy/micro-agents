# Micro-Agents — Backlog

This file tracks implementation work and its completion status. Completed work
is summarized in [CHANGELOG.md](CHANGELOG.md); implementation evidence and
limitations belong in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Backlog audited against `main` (v0.1.7) on 2026-11-08.

## Project scope and release policy

The default release path is GitHub Releases plus signed GHCR and Docker Hub
images. Source checkouts and release artifacts remain supported installation
paths; GHCR is the canonical registry and Docker Hub is the public mirror. The
tag-triggered workflow creates the `micro-agents` Docker Hub repository when it
does not exist and requires only the owner-configured Docker Hub namespace and
access token.

This remains a pre-release reference implementation, not a project-operated
production service. Cloud C0–C5 code is reference/control-plane material, and
cluster admission, provider selectors, security contexts, live promotion, and
rollback are owned by each self-hosting deployment.

## Current status

All repository-owned implementation and documentation tasks in this backlog are
complete. The checked items below record the delivered contract and its
limitations. Docker Hub credentials are owner configuration for the release
workflow; live cluster validation remains an optional self-hosting activity.
Python package publication to PyPI is outside the current distribution plan.

## Standalone framework backlog

### P1 — Runtime gap-filling layer (custom implementation over the abstraction)

Goal: the runtime registry already makes frameworks swappable, but each adapter
reports a different capability set (LangGraph has no checkpoint/resume or
approval pauses; Google ADK has its own session layout and approval storage and
no `ApprovalStore`). Micro-Agents should own those semantics once, in a
project-owned layer between the bootstrap and the framework, so a definition
behaves the same on every runtime and a framework adapter only supplies
orchestration. Design rule: fill gaps at the **boundaries Micro-Agents already
controls** (tools, model provider, stores, request/response), never by reaching
into framework internals. See [Runtimes](docs/RUNTIMES.md) and
[ADR 0021](docs/adr/0021-runtime-registry-and-langgraph-adapter.md).

Current gaps this plan closes (from the capability matrix in `docs/RUNTIMES.md`):

| Gap | custom | google-adk | langgraph |
|---|---|---|---|
| Durable approval continuations via `ApprovalStore` | yes | no (ADK-native, in session) | no (fails closed) |
| Checkpoint / resume via `CheckpointStore` | yes | yes (ADK-specific) | no |
| Uniform session transcript layout | yes | no (ADK layout) | yes |
| Governance shared without inheriting `AdkRuntime` | n/a | partial | no (subclass, private helpers) |

#### Phase 0 — Specify the contract (design, no behavior change)

- [x] Write ADR 0022: the gap-filling layer. Decide the three mechanisms below
      (governed tools, governed model, runtime decorator), what is guaranteed
      identically on every runtime, and what stays framework-native (for
      example ADK's own session transcript). Record the rejected alternative of
      per-adapter reimplementation.
- [x] Extend `RuntimeCapabilities` to separate **native** from **provided**
      support (for example `checkpointing: native | provided | none`), keeping
      `as_dict()` backwards compatible and the schema/status docs in sync.
- [x] Define the "uniform guarantees" list and make it the conformance contract:
      policy deny/approve, argument validation, idempotent operations, audit and
      telemetry events, retry/fallback/circuit breaker, deadline, session replay,
      checkpoint/resume, approval pause/resume, max-iteration bound.

#### Phase 1 — Neutral governance core (remove the `AdkRuntime` inheritance)

- [x] Extract the framework-independent logic now living in `runtimes/adk`
      (`_execute_tools`, `_invoke` retry/fallback/breaker, `_system_prompt`,
      `_resolve_tools`, deadline, call-id helpers) into
      `micro_agent/runtime/governance.py` as composable pieces with no framework
      imports; keep `runtimes/adk` as the reference loop that uses them.
- [x] Rebase `LangGraphRuntime` on the neutral core instead of subclassing
      `AdkRuntime`; delete its imports of private `runtimes.adk.runtime` names
      and tighten the `runtimes.langgraph` coupling cap in
      `tests/test_dependency_rules.py`.
- [x] Rename the misleading `runtimes/adk` package/module surface (it is the
      custom loop) behind a compatibility alias, per ADR 0008, with a
      deprecation note and changelog entry.
- [x] Guard with tests: behavior-preserving refactor, all 21 conformance cases
      and the existing runtime tests pass unchanged.

#### Phase 2 — Governed tools (works on any framework)

- [x] Add `GovernedTool`: wraps a `Tool` so policy check, argument validation,
      idempotent operation handling, audit and telemetry happen at the tool
      boundary no matter which framework invokes it. Frameworks receive wrapped
      tools; they cannot bypass governance.
- [x] Add the approval gate at that boundary: a tool that needs approval raises
      a framework-neutral `ApprovalRequired(continuation_id, tool, arguments)`;
      the adapter converts it to a stop, and the layer persists the pending
      request in the `ApprovalStore`. Replaces LangGraph's fail-closed denial
      and ADK's session-only approval state.
- [ ] Route the Google ADK adapter's tools through `GovernedTool` and make its
      approval path use `ApprovalStore`; remove the "`MICRO_AGENT_APPROVAL_ENDPOINT`
      is rejected for ADK" restriction once parity tests pass (keep ADK-native
      continuations only as an opt-in `native_approvals` setting).
- [ ] Tests: the same denied / approval-required / idempotent-retry scenarios
      pass unchanged on custom, google-adk and langgraph.

#### Phase 3 — Provided checkpoint and resume

- [ ] Add a `CheckpointingRuntime` decorator that implements `checkpoint_id`
      resume for any runtime lacking native support: it snapshots the neutral
      invocation state (request, transcript so far, tool results, iteration
      count) to `CheckpointStore` at tool-round boundaries and replays it into a
      fresh framework invocation on resume.
- [ ] Define the neutral invocation-state schema (versioned, JSON, secret-free)
      and a migration rule; reject checkpoints written by an incompatible
      version.
- [ ] Wire it through the registry: `RuntimeRegistration.provides` lists the
      gaps the layer may fill; bootstrap wraps the runtime only for capabilities
      it lacks and reports them as `provided` in `capabilities()`.
- [ ] Tests: kill-and-resume mid tool loop on langgraph and google-adk using the
      in-memory and SQLite `CheckpointStore`; resume is exactly-once for
      idempotent operations.

#### Phase 4 — Governed model boundary and session uniformity

- [ ] Add a `GovernedModelProvider` wrapper (retry, fallback, circuit breaker,
      deadline, usage and cost accounting, redaction) so adapters that call the
      Micro-Agent provider SPI get identical behavior, and the google-adk native
      Gemini path reports the same usage metadata.
- [ ] Normalize the stored session transcript to one Micro-Agent layout
      regardless of runtime (an adapter-side mapper for ADK's layout) so
      sessions are portable when an operator changes `MICRO_AGENT_RUNTIME`;
      document the migration and add a cross-runtime session round-trip test.

#### Phase 5 — Conformance and documentation

- [ ] Grow `tests/test_runtime_conformance.py` from 7 to a full matrix covering
      every item in the uniform-guarantees list; a capability reported `provided`
      must pass the same cases as `native`.
- [ ] Add a "capability gaps" report command or doc table generated from
      `RuntimeCapabilities` so `docs/RUNTIMES.md` cannot drift from code.
- [ ] Update `docs/RUNTIMES.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/STANDARDS.md`,
      README and CHANGELOG; document how a third-party adapter opts into the layer.
- [ ] Add the `langgraph` job (and a combined `adk`+`langgraph` parity job) to
      the required-checks ruleset (owner action).

#### Risks and open questions

- ADK runs its own tool loop; wrapping tools may not cover ADK-native
  confirmation flows. Decide in ADR 0022 whether ADK keeps native approvals as
  the default and `ApprovalStore` as an opt-in.
- Checkpointing at tool-round boundaries cannot resume inside a single model
  call or a streaming response; state this explicitly rather than claim it.
- Idempotency for resumed side effects depends on `OperationRegistry`; resume
  must reuse operation keys, so key derivation needs to be stable across
  frameworks.

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
      self-hosting-cluster OpenShift SecurityContextConstraints remain a deployment
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
      streaming.
- [x] Add native Google Gemini and Azure OpenAI model adapters. Gemini speaks
      `generateContent`/SSE with tool translation and header-only API keys;
      Azure OpenAI reuses the OpenAI-compatible translation with deployment URLs,
      `api-version`, and `api-key` auth. Vertex AI (OAuth/ADC) and Entra ID
      bearer authentication remain deployment-owned via an injected signing
      client. See [ADR 0020](docs/adr/0020-native-model-providers-and-adk-approval-state.md).
- [x] Remove the `core`↔`runtime` import cycle that made
      `import micro_agent.runtime` fail in a fresh interpreter, and guard every
      package with an isolated-interpreter import test.
- [x] Resolve the Google ADK approval-store gap by design: ADK approval
      continuations live in the durable ADK session transcript, and bootstrap
      fails fast if `MICRO_AGENT_APPROVAL_ENDPOINT` is combined with the ADK
      runtime. See ADR 0020.
- [x] Put an abstraction layer between the bootstrap and runtimes: a runtime
      registry (`RuntimeRegistration`, `RuntimeBuildContext`, entry-point group
      `micro_agent.runtimes`) replaces the hard-coded Google ADK branch, so
      Google ADK is one adapter among several. See
      [Runtimes](docs/RUNTIMES.md) and
      [ADR 0021](docs/adr/0021-runtime-registry-and-langgraph-adapter.md).
- [x] Add a second framework adapter, LangGraph (`runtimes/langgraph`), driven
      by a real `StateGraph`, plus a conformance suite run against the custom,
      Google ADK and LangGraph runtimes.
- [x] Superseded by "P1 — Runtime gap-filling layer" below: the LangGraph
      checkpoint and approval gaps are closed by the project-owned layer, not by
      LangGraph-specific code.
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
- [x] Define the self-hosted supply-chain admission contract: operators should
      require immutable image digests, the release workflow's Cosign identity,
      SLSA provenance, and SPDX SBOM attestations before admission. This
      repository operates no production cluster, so live enforcement,
      promotion, and rollback remain deployment-owner validation.

### P2 — Benchmarks

- [x] Add an external benchmark harness for live-model, network, and tool
      latency through deployed HTTP/MCP endpoints. Existing CI remains fake and
      deterministic; production baselines remain deployment-owned.
- [x] Add an operator-invoked rising-concurrency capacity matrix for deployed
      HTTP/MCP front doors. Each stage reports errors, p95 latency, and
      throughput and can record replica/shared-state metadata; live Redis/
      Postgres execution and SLO sign-off remain deployment-owned.

## Micro-Agent Cloud — C5 production hardening

Cloud C0–C4 reference slices and SQLite durability reference backends are
implemented in the top-level `cloud` package; further shared-service operation
and production validation remain deployment-owner work.

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
- [x] Evaluate splitting `cloud` into its own repository and deployment
      package. [ADR 0018](docs/adr/0018-cloud-repository-boundary.md) defers the
      split until contracts, ownership, independent release automation, and
      CI boundaries stabilize.

## Deferred / non-goals

- LangChain model abstractions (adapters call the Micro-Agent provider SPI)
- visual designer
- workflow engine
- agent marketplace
- distributed memory platform
- PyPI publication as a distribution channel; container images and GitHub
  Release artifacts are the supported distribution paths.
- Operating a project-hosted production cluster or managed promotion service.
