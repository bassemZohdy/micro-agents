# Micro-Agents — Backlog

This file contains open work only. Completed work belongs in
[CHANGELOG.md](CHANGELOG.md); evidence and limitations belong in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).

Baseline audited: `dabf50b` on 2026-09-01. Completed work is removed rather
than retained as checked boxes.

## Release gate

Do not describe the framework as production-ready, publish a stable release,
or start Micro-Agent Cloud implementation until all P0 items are complete and
the relevant P1 acceptance tests are green.

## P0 — Correctness and truthful runtime

### P0.1 Implement a genuine Google ADK runtime

- [x] Keep the current custom loop as the built-in runtime and add a separate
      Google ADK adapter package.
- [x] Add and pin the supported `google-adk` dependency as an optional `adk`
      extra.
- [x] Map definition behavior, the model-provider bridge, native tools,
      sessions, invocation deadlines, and terminal responses to ADK-native
      constructs without leaking ADK types through the SPI.
- [x] Add ADK lifecycle, invocation, tool-call, session, and failure tests.
- [x] Select the Google ADK adapter from executable deployment configuration.
- [x] Map MCP, memory, policy, and OpenTelemetry services into the selected
      adapter; unsupported declarations continue to fail fast.
- [x] Publish a runtime capability matrix and reject unsupported required
      capabilities at startup.

Acceptance: tests prove ADK objects are constructed and invoked while the same
definition remains runtime-neutral.

### P0.2 Build the production bootstrap/runtime factory

- [x] Construct the built-in tool registry, MCP connection manager, telemetry
      (configured log level), and injected policy from configuration; declared
      tools are validated against the constructed registry before runtime
      creation.
- [x] Construct the built-in memory and in-memory/SQLite session providers
      from definition and endpoint bindings; reject unsupported external state
      bindings before runtime creation.
- [x] Probe the configured model, state providers, and declared MCP servers
      before marking the agent ready; startup failures remain non-ready.
- [x] Construct knowledge and non-environment credential providers from
      configuration; every declared credential reference must resolve before
      runtime creation, and declared knowledge sources are health-checked at
      startup.
- [x] Validate knowledge and credential providers once those integrations are
      constructed.
- [x] Remove or wire every currently dead `MICRO_AGENT_*` variable.

Acceptance: the same image starts in explicit fake mode and in a real
OpenAI-compatible configuration using only external configuration changes.

### P0.3 Complete invocation concurrency controls

- [x] Propagate one invocation deadline through model, tool, MCP, and state
      calls; cancellation reaches the active provider operation.

Acceptance: concurrency is bounded, cancellation releases resources, and
shutdown either drains within its deadline or cancels remaining work safely.

### P0.4 Enforce transport security and policy references

- [x] Add authentication middleware with validated caller and user/tenant
      identity: an `Authenticator` SPI configured through `MICRO_AGENT_AUTH`
      (OIDC/OAuth2 Bearer JWT implemented first as the dominant scheme),
      stable 401 responses, public health/discovery routes, and fail-fast
      startup when the definition requires caller identity without an
      authenticator.
- [x] Propagate verified caller identity through model, tool, and MCP
      operations: the runtime binds caller/user identity into an invocation
      context (contextvar-based, the same mechanism future OpenTelemetry
      trace propagation uses) and workload identity resolves from
      environment overrides and the Kubernetes service-account mount.
- [x] Resolve `security.policy_refs`, `credential_refs`, model credentials,
      and MCP credentials through configured providers; unresolvable
      references and unresolvable policy declarations fail before runtime
      creation.
- [x] Stop treating caller-supplied metadata as identity; a source-level
      guard keeps request metadata out of identity construction.
- [x] Evaluate skills and model restrictions as well as tools and MCP servers.
- [x] Implement approval/confirmation continuation in the built-in runtime
      instead of converting `approval_required` into a permanent denial:
      invocations pause with a continuation id and resume on approve/deny;
      hard policy denials still apply to approved requests.
- [ ] Map approval continuation onto the Google ADK adapter's native
      tool-confirmation mechanism.
- [x] Make policy decisions and audit events durable and redacted: an
      `AuditSink` SPI records policy denials, approval decisions, and
      authentication failures as redacted JSON lines (stdout by default for
      platform collection, or a file sink).

Acceptance: unauthorized calls fail before model invocation; authorized
delegated calls preserve verified identity through model/tool/MCP operations.

## P1 — Standards and production integrations

### P1.1 A2A v1.0.1 compliance

- [x] Replace `/.well-known/agent.json` with the standard
      `/.well-known/agent-card.json` route.
- [x] Replace the project-local card shape with the official SDK card model,
      including protocol binding/version (`preferredTransport`,
      `protocolVersion`), security schemes (advertised from the configured
      authenticator), input/output modalities, and complete skill metadata.
- [x] Implement the JSON-RPC binding and a complete non-streaming
      task/message lifecycle (submitted → working → completed/failed) over
      `message/send`.
- [x] Add authentication at the HTTP transport layer; A2A interactions are
      guarded by the same transport authentication as the native API when
      caller identity is required.
- [x] Validate server and card with the official A2A Python SDK: the card is
      the SDK model served by the SDK server stack, and the acceptance run
      uses the official client (resolver + client) end-to-end without
      project-specific adapters.
- [x] Make the declared protocol version explicit and reject unsupported
      versions (definition declarations are validated at startup; requests
      declaring another version are rejected).

Note: the official SDK's wire protocol version is `0.3.0` (its own version
domain, distinct from the v1.0.1 specification document); declarations are
validated against the versions the installed SDK supports.

Acceptance: an official client resolves the card and completes a
non-streaming task without project-specific adapters — proven by the
official-SDK client integration test.

### P1.2 MCP stable-wire client

- [x] Integrate the official MCP Python SDK against stable specification
      `2025-11-25` (`mcp` 1.26, optional extra) behind the existing
      `McpClient` SPI, selected by the executable bootstrap.
- [x] Support standard stdio and Streamable HTTP configuration; stdio models
      command/arguments separately from HTTP endpoints in the definition.
- [x] Treat legacy SSE compatibility explicitly rather than as a peer stable
      transport (supported for migration, documented as legacy).
- [x] Implement initialization, version/capability negotiation, per-call and
      connect timeouts, and graceful close through the SDK session;
      notifications are consumed by the SDK session.
- [x] Apply allowlists, TLS, SSRF defenses, redirect policy, credential
      injection, response limits, and redaction to the real client.
- [x] Add official-SDK interoperability tests with a real MCP server over
      stdio and Streamable HTTP.
- [x] Add bounded automatic reconnect behavior for dropped server connections;
      explicit shutdown never reconnects and exhausted attempts surface an
      unhealthy client state.

Acceptance: a YAML-only MCP declaration discovers and invokes a real SDK-backed
server through the executable bootstrap.

### P1.3 Correct model tool-call protocol

- [x] Preserve provider tool-call IDs and the assistant `tool_calls` payload
      in conversation history; requests without an id receive a generated one.
- [x] Return tool results with the required `tool_call_id`.
- [x] Validate tool names and JSON Schema inputs (required properties and
      basic types) before execution; schema-invalid calls are rejected back
      to the model without running the tool.
- [x] Add configurable proxy/TLS behavior and injectable HTTP transport.
- [x] Define provider capability negotiation for structured output, streaming,
      and tool use; tool use is enforced at startup (declaring tools against a
      provider without it fails), the other two stay false until those
      features are wired.
- [x] Add a live OpenAI-compatible end-to-end acceptance test: a real server
      completes a multi-turn tool call and the transcript replays from
      session storage.

Acceptance: a real OpenAI-compatible server completes a multi-turn tool call
and the transcript can be replayed from session storage.

### P1.4 Contract and semantic validation

- [x] Enforce definition input and output contracts at the HTTP/runtime
      boundary.
- [x] Add uniqueness and format validation for names, skills, tools, MCP refs,
      versions, transports, URLs, scopes, and runtime capabilities.
- [x] Separate model alias/reference from provider model ID.
- [x] Define overlays and environment-specific endpoint bindings without
      mutating the logical definition.
- [x] Add compatibility fixtures and migration guidance for future API
      versions.

### P1.5 Production state providers

- [ ] Add a concurrency-safe PostgreSQL or Redis session provider.
- [ ] Add production memory and operational/idempotency providers.
- [ ] Add optimistic concurrency/versioning and tenant isolation.
- [x] Purge expired in-memory entries consistently and validate memory policy
      bounds.
- [x] Add locking or clearly restrict the SQLite provider to single-process
      development use.
- [ ] Close providers during shutdown and probe them for readiness.

Acceptance: two independent service processes share session and idempotency
state through an external service under concurrent load.

### P1.6 HTTP and health semantics

- [x] Map definition-contract validation errors to a stable HTTP 422 response
      contract.
- [x] Map concurrency overload to HTTP 429 with retry guidance.
- [x] Add authentication middleware and map authentication failures to the
      stable 401 response contract.
- [x] Map authorization, timeout, dependency, and internal errors to stable
      response contracts without leaking exception details.
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
