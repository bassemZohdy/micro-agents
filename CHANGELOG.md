# Changelog

All notable changes to the Micro-Agents project are documented in this file.

## [Unreleased]

### Documentation

- Added docs/OBSERVABILITY.md: the complete operational-metric inventory
  (counters and gauges with labels), scraping guidance for `GET /metrics`,
  recommended dashboard panels (traffic, errors, latency, tools, retries,
  approvals, cost, saturation), and PromQL alert examples keyed to real
  emitted series — with a source-level guard test that fails when an emitted
  metric is missing from the inventory.

### HTTP and API compatibility

- Versioned the native OpenAPI surface under `/v1` and added an API-version
  response header while retaining `/openapi.json` as a compatibility alias.
  CORS is disabled by default but supports explicit allowlists through
  `create_app()` or `MICRO_AGENT_CORS_ORIGINS`.
- Added an injectable synchronous/asynchronous `RateLimiter` hook with stable
  429/503 contracts, retry guidance, and optional rate-limit headers. Requests
  asking for `text/event-stream` are rejected when the selected runtime does
  not advertise streaming; no unsupported streaming capability is claimed.

### Google ADK

- Mapped approval-gated tools onto Google ADK's native experimental
  `ToolConfirmation` protocol. The adapter now emits an
  `approval_required` continuation with pending tools, hints, and payloads;
  callers resume the original ADK session with the continuation id and an
  approve/deny decision, and approved tools execute exactly once. Added an
  ADK integration test for the pause/resume flow.

### Observability

- Added an opt-in OpenTelemetry bridge for traces and metrics with W3C
  trace-context propagation on HTTP, model, MCP, tool, and A2A paths.
  Content attributes are omitted by default, attribute values and metric label
  cardinality are bounded, and OpenAI/MCP outbound requests inject the active
  carrier per call. Existing in-memory telemetry remains available when the
  optional `otel` extra is not installed.
- Added normalized `model_tokens_total` and optional USD
  `model_cost_usd_total` conventions plus a public Prometheus-compatible
  `/metrics` endpoint for the in-memory operational series.

### Safety and retries

- Added explicit `read_only`, `idempotent`, and `unsafe` tool side-effect
  classifications to the v1alpha1 definition and both runtime adapters.
  Legacy and discovered tools default to `unsafe`; read-only tools remain
  subject to tool allow/deny policy but bypass side-effect approval and
  idempotency claims, while idempotent and unsafe tools retain policy and
  operation-registry handling. Operation objects carry the mapped retry
  classification into registry and audit hooks for downstream dispatch and
  audit decisions. The custom runtime suppresses whole-invocation retries once
  a non-read-only tool starts, preventing a later model or operation-record
  failure from replaying an unknown write outcome.
- Added definition-level retry controls: bounded retry attempts and wall-clock
  budgets, exponential backoff, and optional jitter. Defaults preserve the
  existing single immediate retry; circuit breaking and a configurable
  retryable-error taxonomy remain open follow-up work.

### Bootstrap

- Added typed deployment endpoint overlays. `EnvironmentOverlay` binds model,
  MCP, memory, and session locations at bootstrap time without mutating the
  logical definition; model/MCP URLs are validated, unknown MCP refs and stdio
  endpoint overrides fail fast, and explicit environment variables retain
  precedence. Added a canonical v1alpha1 compatibility fixture and migration
  guidance for future definition API versions.
- Hardened the in-memory memory provider: invalid retention bounds now fail at
  construction, and expired entries are purged consistently before reads,
  writes, and capacity eviction so stale data cannot displace live entries.
- Serialized SQLite session-provider operations with an explicit async lock,
  added a bounded busy timeout, made close idempotent, and documented SQLite
  as a single-process development store rather than a production multi-replica
  backend.
- Added an optional Redis-backed session provider for `persistence: external`.
  `redis://` and `rediss://` endpoints are validated at bootstrap; session
  writes use transactional pipelines, Redis key TTLs enforce expiry, stale
  index entries are cleaned, health probing is available, and shutdown closes
  only clients owned by the provider. Install `micro-agents[redis]` to enable
  the built-in client; injected clients remain supported for tests/deployments.
- MCP Streamable HTTP clients now disable ambient proxy environment variables
  by default, keeping loopback/inter-service traffic on the configured endpoint
  unless an explicit transport policy is added.
- Added an optional Redis-backed memory provider. Redis memory stores scoped
  JSON records in a shared namespace, enforces `MemoryPolicy` TTL and capacity
  limits, purges stale index members, exposes a health probe, and closes only
  clients owned by the provider. `MICRO_AGENT_MEMORY_ENDPOINT=redis://...`
  selects it when `micro-agents[redis]` is installed.
- Added an optional Redis-backed operation registry for the custom runtime.
  `MICRO_AGENT_IDEMPOTENCY_ENDPOINT=redis://...` or `rediss://...` enables
  atomic idempotency-key claims, shared in-progress/completed results, result
  TTLs, readiness probing, and ownership-aware shutdown. The Google ADK runtime
  rejects this binding until its distributed mapping is implemented.
- Added verified-tenant namespaces and optimistic versions to in-memory,
  SQLite, and Redis session/memory providers. Provider reads return snapshots,
  updates advance their version, and stale non-zero-version writes raise
  `StateConflictError`; zero-version writes retain legacy compatibility.
- Added A2A compliance on the official a2a-sdk: the standard
  `/.well-known/agent-card.json` route serves the SDK's card model (protocol
  binding/version, security schemes advertised from the configured
  authenticator, input/output modalities, complete skill metadata), and the
  JSON-RPC transport runs a complete non-streaming task lifecycle
  (submitted → working → completed/failed) bridged onto Micro-Agent
  invocations. Declared protocol versions are validated at startup against
  the versions the SDK supports, requests declaring another version are
  rejected, and A2A interactions are guarded by the same transport
  authentication as the native API when caller identity is required. An
  official SDK client resolves the card and completes a task end-to-end in
  the integration suite (optional `a2a` extra).
- Corrected the model tool-call protocol: provider tool-call IDs are
  preserved end-to-end, the assistant `tool_calls` payload stays in the
  conversation history (with generated ids for requests that lack one), and
  tool results carry the required `tool_call_id`. Tool requests are
  validated against the declared JSON Schema (required properties, basic
  types) and rejected back to the model without executing the tool. The
  OpenAI-compatible provider supports explicit proxy and TLS-verification
  configuration plus injectable HTTP clients, and providers now report
  `ProviderCapabilities` — declaring tools against a provider without tool
  use fails at startup.
- Added a live OpenAI-compatible acceptance path: a real loopback server now
  completes a multi-turn tool call, validates the `/v1` endpoint prefix and
  model ID, and confirms that session storage replays the assistant tool-call
  payload together with its matching `tool_call_id` result on the next turn.
- Persisted complete runtime conversation turns (assistant tool calls and tool
  results included) instead of reducing sessions to user/final-assistant
  pairs, so OpenAI-compatible providers can safely replay tool transcripts.
- Added the official MCP SDK wire client behind the existing `McpClient`
  SPI: the executable bootstrap now constructs real SDK-backed connections
  for declared MCP servers against the stable `2025-11-25` specification
  (optional `mcp` extra; without it, startup fails with a clear install
  message). Streamable HTTP and stdio are the standard transports — stdio
  now models a local `command` and `args` in the definition instead of an
  endpoint — and SSE is supported explicitly as a legacy compatibility
  transport. Connections negotiate version/capabilities at initialization,
  bound per-call and connect timeouts, inject credentials at connect time
  (Authorization header for HTTP, credential-named environment variable for
  stdio) without storing them on config objects, and close gracefully. A
  YAML-only MCP declaration now discovers and invokes a real MCP server
  through the executable bootstrap, proven by interop tests over stdio and
  Streamable HTTP with FastMCP servers.
- Added bounded automatic reconnect for unexpected MCP transport termination.
  Reconnect attempts use exponential backoff, preserve negotiated configuration
  and credentials, never run during explicit shutdown, and expose an unhealthy
  terminal state when the retry budget is exhausted.
- Added verified identity propagation: the runtime binds caller and user
  identity into an invocation-scoped context (contextvar-based) so model,
  tool, and MCP operations observe the verified principal without SPI
  signature changes, and resets it when the invocation ends or fails.
  Workload identity resolves per process from `MICRO_AGENT_WORKLOAD_ID`,
  `MICRO_AGENT_WORKLOAD_NAMESPACE`, and `MICRO_AGENT_SERVICE_ACCOUNT`, then
  the Kubernetes service-account namespace mount, then the hostname.
- Added approval/confirmation continuation in the built-in runtime: when a
  policy requires approval for a side-effect operation, the invocation now
  pauses instead of permanently denying — the pending tool requests and
  conversation state are stored under a continuation id (`approval_required`
  response status), and the caller resumes with `approval_decision:
  approve` (executes the pending wave; hard policy denials still apply) or
  `deny` (feeds the model a denial so it can respond). Unknown, expired, or
  foreign continuations fail fast with a stable 404 contract. The approval
  store is an SPI with a dependency-free in-memory default and TTL expiry.
- Added an `AuditSink` SPI for durable, redacted security events: policy
  denials (tool, side effect, skill, model, MCP), approval decisions, and
  authentication failures are recorded as redacted JSON lines — `stdout` by
  default for platform log collection, with `file` and `none` selected
  through `MICRO_AGENT_AUDIT_SINK`/`MICRO_AGENT_AUDIT_FILE`.
- Added transport authentication with verified identity: an `Authenticator`
  SPI selected through external configuration (`MICRO_AGENT_AUTH=oidc` with
  `MICRO_AGENT_AUTH_ISSUER`/`MICRO_AGENT_AUTH_AUDIENCE`), with OIDC/OAuth2
  Bearer JWT validation implemented first as the dominant scheme — asymmetric
  signatures via JWKS, issuer/audience/expiry enforcement, and standard-claim
  mapping onto caller and user/tenant identity. Unauthenticated calls to
  `/v1/invoke` fail with the stable 401 contract before the agent is
  reached; health probes and the discovery card stay public; app creation
  fails fast when the definition requires caller identity but no
  authenticator is configured. Verified identity travels on `AgentRequest`
  (`caller_identity`, `user_context`). PyJWT joins the new optional `auth`
  extra.
- Added deployment-selectable runtime bootstrap via `MICRO_AGENT_RUNTIME`,
  including explicit Google ADK selection and fail-fast validation for ADK
  service declarations that are not mapped yet.
- Added credential providers: `CredentialProvider` with an environment
  default and an injectable non-environment provider
  (`StaticCredentialProvider` for pre-loaded secrets). Every declared
  credential reference — model, MCP server, and security — must resolve
  through the configured provider before runtime creation, and MCP
  connections resolve declared credentials at connect time through the
  manager, never storing them on config objects.
- Added knowledge-provider construction: declared knowledge sources build the
  built-in in-memory retriever (startup health check fails fast until a
  deployment injects one with documents), validated in both runtimes with a
  `knowledge` health probe.
- Added policy-reference resolution: declared `security.policy_refs` resolve
  through an injected policy or a configured policy resolver; unresolved
  references fail before runtime creation in both runtimes.
- Policy enforcement now covers skills and model restrictions (allow/deny
  model sets and provider sets) in addition to tools and MCP servers; denied
  declared skills, models, or MCP servers fail startup deterministically.
- Added a source-level guard and behavioral tests proving caller-supplied
  request metadata is never used to construct caller/user/workload identity.
- The Google ADK adapter now maps declared services onto ADK-native
  constructs: memory maps to an ADK `BaseMemoryService` bridge over the
  Micro-Agent memory provider (auto-store and search included), injected
  policy is enforced deterministically around every ADK tool execution and
  declared MCP server, declared MCP servers connect at startup and surface
  discovered tools as ADK tools, and telemetry records spans, metrics, and
  structured logs around the runner.
- The executable bootstrap now constructs the built-in tool registry, the MCP
  connection manager for declared MCP servers, and telemetry with the
  configured log level, validates declared tools against the constructed
  registry (unresolvable native tools and MCP tools without servers fail
  before runtime creation), and accepts an injected `AgentPolicy` or MCP
  manager for deployments that own those integrations.
- Added executable model bootstrap from definition and `MICRO_AGENT_*`
  bindings, including model ID, provider, endpoint, timeout, generation, and
  environment-backed credentials.
- Added explicit fake/OpenAI-compatible provider selection with fail-fast
  validation for unsupported providers, missing endpoints, bare model
  references, and missing credentials.
- Added bootstrap regression tests and updated the container smoke job to set
  fake mode explicitly.
- Added definition-level invocation concurrency limits with explicit `wait` or
  `reject` overload behavior, including lifecycle stop-race coverage.
- Added `shutdown_timeout_seconds` drain deadlines, cancellation propagation,
  and regression tests for cancelled calls and repeated shutdown.
- Added a shared per-invocation deadline budget, including optional HTTP
  `timeout_seconds`, provider-call cancellation, and stable HTTP 504 deadline
  errors.
- Wired definition-declared in-memory memory and in-memory/SQLite session
  providers into executable bootstrap. SQLite endpoint bindings are honored,
  unsupported external state fails before startup, and configured providers
  close with the runtime.
- Added startup readiness probes for the configured model, state providers,
  and declared MCP servers; dependency failures keep the agent out of READY
  with stable, non-sensitive runtime errors.
- Added the optional pinned `google-adk` extra and a separate
  `runtimes/google_adk` adapter that constructs ADK agents, runners, sessions,
  native tools, and model-provider bridges behind the runtime SPI.

### Documentation

- Reconciled the README, project definition, architecture, Twelve-Factor
  model, ADRs, examples, contributor guide, and deployment guidance with an
  implementation audit at commit `bcfb453`.
- Added getting-started, configuration, HTTP API, deployment, standards, and
  implementation-status guides.
- Rebuilt `TODO.md` as a prioritized open backlog with evidence-based
  acceptance criteria and explicit production release gates.
- Set A2A v1.0.1 and MCP `2025-11-25` as the stable compatibility targets.

### Audit corrections

- `runtimes/adk` remains a custom model/tool loop; the separate optional
  `runtimes/google_adk` adapter is the only package making Google ADK support
  claims.
- The CLI resolves fake or OpenAI-compatible model providers and built-in state
  providers from configuration, but does not yet construct MCP, policy,
  knowledge, external state, or non-environment credential services.
- The current A2A-shaped discovery route and fake MCP client prove internal
  seams, not standards compliance.
- SQLite proves local file persistence only, and the in-tree telemetry facade
  is not OpenTelemetry export or propagation.

### Fixed

- Made `DefaultMicroAgent` lifecycle concurrency-safe without a process-wide
  READY/RUNNING transition; invocation failures no longer poison the agent,
  and stop waits for active invocations to drain.
- Generate request IDs when HTTP callers omit them and return HTTP 503 for an
  unhealthy readiness result.
- Remove multiple expired in-memory sessions without mutating the session map
  during iteration.
- Add regression coverage for concurrency, failure recovery, shutdown drain,
  request IDs, readiness status, and multiple expired sessions.
- Enforce declared input and output contracts before and after every runtime
  invocation, with stable HTTP 422 diagnostics.
- Add definition semantic validation for portable names, semantic versions,
  references, transports, URLs, scopes, capabilities, and duplicate entries.
- Add stable HTTP 429 overload responses with retry guidance and reject
  oversized request bodies before JSON parsing.
- Enforce declared runtime capabilities at startup and expose the complete
  capability matrix through the capabilities endpoint.
- Separate logical model references from provider-specific model IDs in the
  definition and executable bootstrap.
- Add stable HTTP 403/503/500 mappings for authorization, dependency, and
  unexpected runtime failures, plus a reserved 401 authentication mapping that
  never exposes exception details.

### CI and release

- Added the PyYAML typing stubs and upgraded vulnerable test dependencies.
- Split runtime and development dependency audits.
- Added wheel/sdist build, clean-wheel installation, console-entrypoint smoke,
  artifact upload, and strict documentation gates to pull requests.
- Added package metadata and the `micro-agent` console command.
- Made releases validate all quality gates and tag/package version alignment,
  attach distributions and SBOM, and use PyPI trusted publishing without
  masking publication failures.

### Added
- Custom runtime agent loop, currently in the ADK-named package: model call →
  tool execution up to `max_iterations`,
  overall `timeout_seconds`, tool/model timeout enforcement, and
  `error_policy` (fail / retry / fallback) honored from RuntimeSemantics.
- Generic tool resolution from definitions via a built-in registry;
  unresolved tools are reported instead of silently dropped.
- `OpenAICompatProvider` — model provider for a compatible
  `/chat/completions` endpoint, selected by executable definition/environment
  configuration.
- Session integration (history replay/persistence, TTL from definition) and
  memory auto-store behind `MemoryPolicy.auto_store`.
- MCP integration seam: `FakeMcpClient` test double, `McpConnectionManager`
  (factory-injected clients, `server:tool` adapters, resources/prompts
  metadata, health probe, graceful shutdown) and `McpSecurityPolicy` (TLS,
  endpoint allowlist, transport validation, response size limits).
- A2A-shaped project models: `agent_card_from_definition()`,
  `skills_mapping()`, and the preliminary `GET /.well-known/agent.json`
  endpoint; covered by a raw-JSON project test, not official client
  conformance.
- Observability wiring: `Telemetry` facade (StructuredLogger with secret
  redaction + MetricsCollector + span tree) instruments the invocation path —
  invocation count/latency/errors, model latency, tokens, tool calls, policy
  denials; agent/model/tool/MCP spans share trace IDs.
- Active health: dependency probes with `probe_readiness()`, status updates
  after registration, real liveness probe; runtime exposes model/session/
  memory/MCP probes.
- Programmatically injected policy enforcement: `PolicyEvaluator` denies tools/side
  effects before execution and fails startup on denied MCP servers;
  `OperationRegistry` deduplicates idempotent side effects;
  `build_security_context()` loads definition security refs.
- `SqliteSessionProvider` — local development persistence implementation with
  shared-file contract tests.
- Modules moved out of observability: `micro_agent/security/` (identity,
  policy, side effects, context) and `micro_agent/health/` (backward-compat
  re-exports kept).
- Duplicate types consolidated: canonical pydantic `SkillDefinition` and
  `A2AConfig`; skills/interoperability re-export them; conversion helpers
  added (`skills_mapping`, `capability_contract_from_definition`).
- Deploy: `deploy/kubernetes/definition-configmap.yaml` (the deployment's
  missing `micro-agent-definition` ConfigMap).
- Tests: 369 collected (365 default plus four optional Google ADK adapter tests;
  updated unit, integration, and E2E coverage;
  marker groups overlap) including behavioral
  runtime tests, factory-injected MCP configuration, real-socket network
  service, and shared-file SQLite session behavior.
- CI workflows: unit, integration, E2E, package/container smoke, separate
  dependency audits, SBOM, strict docs build/publish, and a gated release to
  PyPI/GHCR/GitHub Releases.
- ADRs 0001–0008 (`docs/adr/`) and MkDocs site configuration.

## [0.1.0] — 2026-08-30

### Milestone 0 — Project Foundation
- Git repository with Apache 2.0 license
- CONTRIBUTING.md, README.md, PROJECT_DEFINITION.md, TODO.md
- ADR directory structure
- Python package management (pyproject.toml, hatchling)
- Formatting (ruff), linting (ruff), static typing (mypy strict)
- Unit testing (pytest, pytest-asyncio, pytest-cov)
- CI/CD (GitHub Actions: lint, typecheck, test, security scanning)
- Dependency scanning (pip-audit)

### Milestone 1 — Micro-Agent Architecture
- Architecture document (docs/architecture/MICRO_AGENT_ARCHITECTURE.md)
- Micro-Agent definition, bounded capability, independent deployment/scaling
- Disposable runtime, explicit identity, capability contract
- Bounded autonomy, externalized state, safe side effects
- Cloud-native principles, distributed system implications
- Reference architecture, non-goals, qualification criteria

### Milestone 2 — Twelve-Factor Micro-Agent Model
- Twelve-Factor Micro-Agent document (docs/architecture/TWELVE_FACTOR_MICRO_AGENT.md)
- All 12 original factors mapped with implementation implications
- 8 agent-specific factors defined (Identity, Capability Contract, Bounded Autonomy, etc.)

### Milestone 3 — Micro-Agent Definition v1alpha1
- Typed Python models (Pydantic) in micro_agent/definition/models.py
- YAML loader with validation and diagnostics
- JSON Schema generation (by_alias=True for apiVersion)
- YAML examples (residency-renewal.yaml, notification-agent.yaml)
- Rejects unknown properties, versioned schema (microagents.io/v1alpha1)

### Milestone 4 — Configuration Framework
- YAML loader, environment-variable overrides
- Secret-reference model (env source)
- Configuration precedence (Defaults → Definition → Environment → Secrets)
- Validation and diagnostics
- Multiple env vars supported (MODEL_ENDPOINT, LOG_LEVEL, MODEL_API_KEY, etc.)

### Milestone 5 — Core Programming Model
- MicroAgent ABC, DefaultMicroAgent concrete implementation
- AgentRequest, AgentResponse, AgentContext, AgentCapabilities, AgentIdentity
- AgentState lifecycle (CREATED → INITIALIZED → STARTING → READY → RUNNING → STOPPED)
- Full lifecycle management with state validation

### Milestone 6 — Runtime SPI
- AgentRuntime ABC, RuntimeAgent handle, RuntimeCapabilities
- create/start/invoke/stop/capabilities operations
- No framework-native types cross the public boundary

### Milestone 7 — Model Support
- ModelConfig, ModelProvider ABC, ModelResponse
- FakeModelProvider (deterministic, configurable responses/errors/tool-requests)
- Invocation recording, usage tracking, health check

### Milestone 8 — Tools
- Tool ABC, ToolMetadata, ToolInputSchema, ToolOutputSchema
- ToolResult, ToolError
- EchoTool (deterministic example tool)

### Milestone 9 — MCP
- McpConfig, McpClient ABC, McpDiscovery
- McpTool, McpResource, McpPrompt
- McpConnectionState

### Milestone 10 — Skills and Capability Contract
- SkillDefinition, CapabilityContract
- has_skill, find_skill, skills_by_tag queries
- Distinguishes skills from tools

### Milestone 11 — Session
- SessionContext, SessionMetadata, SessionProvider ABC
- InMemorySessionProvider (create, get, update, delete, list_active)

### Milestone 12 — Memory
- MemoryEntry, MemoryPolicy, MemoryProvider ABC
- InMemoryMemoryProvider (store, search, get, delete, list_entries)
- Scope-based filtering

### Milestone 13 — Knowledge
- KnowledgeSource, KnowledgeEntry, KnowledgeRetriever ABC

### Milestone 14 — ADK-Named Runtime Vertical Slice
- AdkRuntime implementing AgentRuntime
- Uses FakeModelProvider for CI (no paid model required)
- Custom agent construction, model binding, and lifecycle; Google ADK is not
  integrated

### Milestone 15 — Runtime HTTP API
- FastAPI application (create_app factory)
- POST /v1/invoke, GET /health/live, GET /health/ready, GET /v1/capabilities
- Pydantic request/response models
- Proper JSON serialization (asdict for nested dataclasses)

### Milestone 16 — Health and Readiness
- HealthChecker, HealthStatus, LivenessResult, ReadinessResult
- DependencyHealth tracking
- Liveness and readiness checks

### Milestone 17 — Observability
- StructuredLogger (JSON structured logging with context)
- MetricsCollector (in-memory metrics with labels)
- TraceSpan (distributed tracing spans with events)
- Agent ID, version, invocation ID, session ID in log context

### Milestone 18 — Identity and Security Context
- AgentIdentity (from core), CallerIdentity, UserContext, RuntimeIdentity
- SecurityContext with policy and credential refs
- Agent identity != user identity

### Milestone 19 — Bounded Autonomy and Policy
- AgentPolicy, PolicyRule, PolicyEffect
- PolicyEvaluator (skill, tool, MCP, side-effect evaluation)
- Allowed/denied lists, approval requirements

### Milestone 20 — Safe Side Effects
- Operation, OperationResult, OperationRegistry
- RetryClassification (safe, unsafe, idempotent)
- Idempotency key support, deduplication

### Milestone 21 — A2A
- AgentCard, AgentSkill, A2AConfig
- A2AMessage, A2ATask, A2AResponse

### Milestone 22 — Containerization
- Production Dockerfile (non-root, slim base)
- .dockerignore
- HEALTHCHECK, ENTRYPOINT with --definition arg

### Milestone 23 — Kubernetes-Oriented Baseline
- Deployment (2 replicas, rolling update, health probes, resources)
- Service (ClusterIP)
- ConfigMap, Secret references
- readOnlyRootFilesystem with emptyDir for /tmp

### Milestone 24 — Architecture Validation
- Two example agents (residency-renewal, notification-agent)
- Validation tests for bounded responsibility, independent deployment
- External state, explicit identity/skills, MCP integration

### Milestone 25 — Definition Portability Review
- No ADK-specific types in definition
- Mandatory/optional semantics documented
- Compatibility/versioning rules defined

### Milestone 26 — CI/CD and Release
- Unit tests (232 passing, 98% coverage)
- Security scanning (pip-audit)
- Dependency scanning
- CHANGELOG.md (this file)
- py.typed marker for downstream consumers

### Process Entrypoint
- micro_agent/__main__.py (python -m micro_agent --definition ...)
- FastAPI HTTP server with uvicorn
- Graceful lifecycle (initialize → start → serve → stop → shutdown)

### Known Limitations

The 0.1.0 slice was intentionally minimal. Later work implemented useful
interfaces and test seams, but the following production boundaries remain:

- MCP ships a client integration seam (manager + security + fake client), but
  no production wire-protocol client or official-SDK interoperability test
- OpenTelemetry export is not integrated yet; the in-tree `Telemetry` facade
  is the single swap point
- the current A2A-shaped discovery path/card is not A2A v1 compliant and no
  full task protocol is implemented
- executable provider/configuration/state/security bootstrap remains absent
