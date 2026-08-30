# Changelog

All notable changes to the Micro-Agents project are documented in this file.

## [Unreleased]

### Added
- ADK runtime agent loop: model call → tool execution up to `max_iterations`,
  overall `timeout_seconds`, tool/model timeout enforcement, and
  `error_policy` (fail / retry / fallback) honored from RuntimeSemantics.
- Generic tool resolution from definitions via a built-in registry;
  unresolved tools are reported instead of silently dropped.
- `OpenAICompatProvider` — real model provider for any /chat/completions
  endpoint, selected by configuration (fake model remains the CI default).
- Session integration (history replay/persistence, TTL from definition) and
  memory auto-store behind `MemoryPolicy.auto_store`.
- MCP integration: `FakeMcpClient` test double, `McpConnectionManager`
  (connect-by-configuration, `server:tool` adapters, resources/prompts
  metadata, health probe, graceful shutdown) and `McpSecurityPolicy` (TLS,
  endpoint allowlist, transport validation, response size limits).
- A2A: `agent_card_from_definition()`, `skills_mapping()`, and the
  `GET /.well-known/agent.json` endpoint; validated by an independent raw-JSON
  HTTP client test.
- Observability wiring: `Telemetry` facade (StructuredLogger with secret
  redaction + MetricsCollector + span tree) instruments the invocation path —
  invocation count/latency/errors, model latency, tokens, tool calls, policy
  denials; agent/model/tool/MCP spans share trace IDs.
- Active health: dependency probes with `probe_readiness()`, status updates
  after registration, real liveness probe; runtime exposes model/session/
  memory/MCP probes.
- Policy enforcement in the runtime: `PolicyEvaluator` denies tools/side
  effects before execution and fails startup on denied MCP servers;
  `OperationRegistry` deduplicates idempotent side effects;
  `build_security_context()` loads definition security refs.
- `SqliteSessionProvider` — persistent reference implementation proving the
  multi-replica shared-session acceptance.
- Modules moved out of observability: `micro_agent/security/` (identity,
  policy, side effects, context) and `micro_agent/health/` (backward-compat
  re-exports kept).
- Duplicate types consolidated: canonical pydantic `SkillDefinition` and
  `A2AConfig`; skills/interoperability re-export them; conversion helpers
  added (`skills_mapping`, `capability_contract_from_definition`).
- Deploy: `deploy/kubernetes/definition-configmap.yaml` (the deployment's
  missing `micro-agent-definition` ConfigMap).
- Tests: 299 total (246 unit + 53 integration/e2e) including behavioral
  runtime tests, MCP-by-configuration, real-socket network service, and
  multi-replica session acceptance.
- CI: integration and e2e jobs, container build + smoke test, SBOM, docs
  publishing (mkdocs), `release.yml` (tag → PyPI/GHCR + generated notes);
  `runtimes/` now strict-type-checked.
- ADRs 0001–0006 (docs/adr/) and mkdocs site config.

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

### Milestone 14 — ADK Runtime Vertical Slice
- AdkRuntime implementing AgentRuntime
- Uses FakeModelProvider for CI (no paid model required)
- Agent construction, model binding, lifecycle

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

### Milestone 23 — Kubernetes/OpenShift Baseline
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

### Known Limitations (resolved in Unreleased)

The 0.1.0 slice was intentionally minimal; the interface-only stubs listed
below have since been implemented — see the Unreleased section and ADRs
0002/0003 for the remaining boundaries:

- MCP ships a client integration seam (manager + security + fake client); a
  production wire-protocol client (official SDK) plugs in via the manager's
  client factory
- OpenTelemetry export is not integrated yet; the in-tree `Telemetry` facade
  is the single swap point
- A2A covers discovery (agent card) but not the full A2A task protocol
