# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); evidence and limitations belong in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Baseline audited: `fd6eee8` on 2026-08-30. Completed work is removed rather
than retained as checked boxes.

## Release gate

Do not describe the framework as production-ready, publish a stable release,
or start Micro-Agent Cloud implementation until all P0 items are complete and
the relevant P1 acceptance tests are green.

## P0 — Correctness and truthful runtime

### P0.1 Implement a genuine Google ADK runtime

- [ ] Decide the package boundary: rename the current custom loop to a built-in
      runtime or replace its internals with Google ADK.
- [ ] Add and pin the supported `google-adk` dependency.
- [ ] Map definition behavior, model, tools, sessions, and runtime semantics to
      ADK-native constructs without leaking ADK types through the SPI.
- [ ] Add ADK lifecycle, invocation, tool-call, session, and failure tests.
- [x] Publish a runtime capability matrix and reject unsupported required
      capabilities at startup.

Acceptance: tests prove ADK objects are constructed and invoked while the same
definition remains runtime-neutral.

### P0.2 Build the production bootstrap/runtime factory

- [ ] Construct tool registry, MCP client, session, memory, knowledge, policy,
      telemetry, and credential providers from configuration.
- [ ] Validate all required dependencies before readiness.
- [ ] Add production MCP, state, knowledge, policy, telemetry, and credential
      providers to the bootstrap; unsupported network endpoints must remain
      fail-fast until their implementations exist.

Acceptance: the same image starts in explicit fake mode and in a real
OpenAI-compatible configuration using only external configuration changes.

### P0.3 Complete invocation concurrency controls

- [x] Propagate one invocation deadline through model, tool, MCP, and state
      calls; cancellation reaches the active provider operation.

Acceptance: concurrency is bounded, cancellation releases resources, and
shutdown either drains within its deadline or cancels remaining work safely.

### P0.4 Enforce transport security and policy references

- [ ] Add authentication middleware and validated caller/client, user/tenant,
      and workload identity propagation.
- [ ] Resolve `security.policy_refs`, `credential_refs`, model credentials,
      and MCP credentials through configured providers.
- [ ] Stop treating caller-supplied metadata as identity.
- [ ] Evaluate skills and model restrictions as well as tools and MCP servers.
- [ ] Implement approval/confirmation continuation instead of converting
      `approval_required` into a permanent denial.
- [ ] Make policy decisions and audit events durable and redacted.

Acceptance: unauthorized calls fail before model invocation; authorized
delegated calls preserve verified identity through model/tool/MCP operations.

## P1 — Standards and production integrations

### P1.1 A2A v1.0.1 compliance

- [ ] Replace `/.well-known/agent.json` with the standard
      `/.well-known/agent-card.json` route.
- [ ] Replace the project-local card shape with the v1 card model, including
      `supportedInterfaces`, protocol binding/version, security schemes,
      modalities, and complete skill metadata.
- [ ] Implement at least one complete standard binding and task/message
      lifecycle, not discovery only.
- [ ] Add authentication at the HTTP transport layer.
- [ ] Validate server and card with the official A2A Python SDK.
- [ ] Make the declared protocol version explicit and reject unsupported
      versions.

Acceptance: an official v1.0.1 client resolves the card and completes a
non-streaming task without project-specific adapters.

### P1.2 MCP stable-wire client

- [ ] Integrate the official MCP Python SDK against stable specification
      `2025-11-25`.
- [ ] Support standard stdio and Streamable HTTP configuration; model stdio
      command/arguments separately from HTTP endpoints.
- [ ] Treat legacy SSE compatibility explicitly rather than as a peer stable
      transport.
- [ ] Implement initialization, version/capability negotiation, cancellation,
      timeouts, reconnect behavior, notifications, and graceful close.
- [ ] Apply allowlists, TLS, SSRF defenses, redirect policy, credential
      injection, response limits, and redaction to the real client.
- [ ] Add official-SDK interoperability tests with a real MCP server.

Acceptance: a YAML-only MCP declaration discovers and invokes a real SDK-backed
server through the executable bootstrap.

### P1.3 Correct model tool-call protocol

- [ ] Preserve provider tool-call IDs and the assistant `tool_calls` payload
      in conversation history.
- [ ] Return tool results with the required `tool_call_id`.
- [ ] Validate tool names, JSON Schema inputs, outputs, and provider response
      shapes.
- [ ] Add configurable proxy/TLS behavior and injectable HTTP transport.
- [ ] Define provider capability negotiation for structured output, streaming,
      and tool use.

Acceptance: a real OpenAI-compatible server completes a multi-turn tool call
and the transcript can be replayed from session storage.

### P1.4 Contract and semantic validation

- [x] Enforce definition input and output contracts at the HTTP/runtime
      boundary.
- [x] Add uniqueness and format validation for names, skills, tools, MCP refs,
      versions, transports, URLs, scopes, and runtime capabilities.
- [x] Separate model alias/reference from provider model ID.
- [ ] Define overlays and environment-specific endpoint bindings without
      mutating the logical definition.
- [ ] Add compatibility fixtures and migration guidance for future API
      versions.

### P1.5 Production state providers

- [ ] Add a concurrency-safe PostgreSQL or Redis session provider.
- [ ] Add production memory and operational/idempotency providers.
- [ ] Add optimistic concurrency/versioning and tenant isolation.
- [ ] Purge expired in-memory entries consistently and validate memory policy
      bounds.
- [ ] Add locking or clearly restrict the SQLite provider to single-process
      development use.
- [ ] Close providers during shutdown and probe them for readiness.

Acceptance: two independent service processes share session and idempotency
state through an external service under concurrent load.

### P1.6 HTTP and health semantics

- [x] Map definition-contract validation errors to a stable HTTP 422 response
      contract.
- [x] Map concurrency overload to HTTP 429 with retry guidance.
- [ ] Map authentication, authorization, timeout, dependency, and internal
      errors to stable response contracts.
- [x] Add a default request-size guard.
- [ ] Add CORS policy, rate-limiting integration points, and streaming only
      when runtime-supported.
- [ ] Document and version the public API.

### P1.7 OpenTelemetry and operational observability

- [ ] Replace in-memory-only telemetry with OpenTelemetry traces, metrics, and
      context propagation while keeping test exporters.
- [ ] Propagate trace context through HTTP, model, MCP, tool, and A2A calls.
- [ ] Define safe content-capture defaults, cardinality limits, and cost/token
      conventions.
- [ ] Expose operational metrics and document dashboards/alerts.

### P1.8 Side-effect and retry safety

- [ ] Classify tools as read-only, idempotent, or unsafe instead of treating
      every tool as a side effect.
- [ ] Persist idempotency records atomically with status and expiry.
- [ ] Do not retry an entire invocation after an unknown write outcome.
- [ ] Add backoff, jitter, retry budgets, circuit breaking, and error
      classification.
- [ ] Test crash/replay and partial-failure scenarios.

### P1.9 Deployment and supply-chain hardening

- [ ] Replace example image tags with immutable version/digest guidance.
- [ ] Make the image compatible with OpenShift arbitrary UIDs; remove fixed
      runtime UID assumptions.
- [ ] Replace the committed empty Secret with a safe template and documented
      secret-manager workflow.
- [ ] Add NetworkPolicy, PodDisruptionBudget, autoscaling guidance, and
      topology spread as optional production examples.
- [ ] Validate manifests in CI and test shutdown/readiness on Kubernetes.
- [ ] Add image signing, provenance/attestation, and a dependency lock
      strategy.

### P1.10 Release correctness

- [ ] Validate schema version, image tags, and changelog alignment in addition
      to the existing tag/package-version check.
- [ ] Protect `main` and release tags with the required CI checks in the GitHub
      ruleset; workflow gates alone do not prevent an administrator bypass.
- [ ] Configure and verify PyPI trusted publishing for this repository before
      creating the first release tag.

## P2 — Framework maturity

- [ ] Replace the single built-in tool map with a documented extension/plugin
      contract.
- [ ] Define knowledge-provider configuration and runtime retrieval semantics.
- [ ] Add structured output, streaming, and checkpointing only behind truthful
      runtime capabilities.
- [ ] Add performance/load benchmarks and resource budgets.
- [ ] Add upgrade, deprecation, and API compatibility policy.
- [ ] Add examples that are executable as written; keep conceptual examples
      clearly labeled.
- [ ] Decide how backward-compatibility re-exports will be deprecated.

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
