# Implementation Status

Last audited: 2026-09-12
Audited implementation revision: current changes derived from `main` @
`973d0f0` on 2026-09-12

This document separates implemented code from architectural intent. Passing
unit tests prove the exercised behavior only; they do not establish production
readiness or protocol compliance.

## Verification snapshot

| Check | Result | Evidence/qualification |
|---|---|---|
| Ruff lint and format | Pass | local and remote CI |
| Tests | 776 collected | 669 passed and 2 skipped in the default CI selection (`not integration`, `not e2e`, and `not otel`); 105 integration/e2e/OTel tests are deselected for their dedicated CI jobs, including real Redis/PostgreSQL state-provider coverage |
| Schema drift | Pass | generated schema matches the tracked file |
| Container smoke | Pass | fake-provider startup and three HTTP endpoints |
| Package build | Pass | wheel/sdist build plus isolated wheel import and console-entrypoint smoke |
| Documentation | Pass | strict MkDocs build on pull requests; publish only from `main` |
| Performance budgets | Pass | deterministic fake-model runtime and HTTP scenarios pass locally; CI enforces both; the external HTTP/MCP harness is operator-invoked |
| Strict type check | Pass | `types-PyYAML` is part of the development extra |
| Dependency audit | Pass | runtime and development environments are audited separately |
| Overall GitHub CI | Pass | [CI run #233](https://github.com/bassemZohdy/micro-agents/actions/runs/34698661072), all required jobs successful |
| Ref protection | Pass | active rulesets `main-required-CI` (15 required CI checks, no deletion/force-push, empty bypass) and `release-tags-immutable` (`v*` tags undeletable and unmovable); the only open release-gate item is the pypi.org-side trusted-publisher entry (an owner action on pypi.org) |

The OpenAI-compatible client defaults to direct connections (`trust_env=False`)
so ambient proxy variables cannot unexpectedly route model traffic or loopback
tests. Deployments that require a proxy must opt in through provider
configuration; proxy policy remains part of production hardening.

## Capability assessment

### Definition and configuration

Implemented:

- strict Pydantic `microagents.io/v1alpha1` model plus a compatible
  `microagents.io/v1beta1` migration path
- YAML loader with diagnostics
- generated draft-2020-12 JSON Schemas for both supported API versions and CI
  drift check
- semantic validation for names, versions, references, transports, URLs,
  scopes, capabilities, and duplicate collections
- runtime-neutral input/output contract enforcement with stable diagnostics
- a separate `resolve_config()` precedence utility
- typed deployment-only `EnvironmentOverlay` endpoint bindings for model, MCP,
  memory, and session services; bindings are validated and applied without
  mutating the logical definition
- canonical v1alpha1 and v1beta1 compatibility fixtures; the beta loader
  migrates camelCase wire fields while rejecting unsupported versions and
  unknown fields
- the bootstrap resolves model provider, endpoint, model ID, and credentials;
  built-in memory and SQLite/in-memory session bindings plus optional Redis
  external memory/session bindings, the built-in/plugin tool registry, MCP
  connection manager, knowledge provider, credential provider, telemetry, and
  the custom runtime's optional Redis operation registry are constructed from
  configuration; unsupported external schemes fail fast

Gaps:

- model alias resolution now has a versioned `ModelCatalog` SPI with injected
  and strict HTTP implementations; broader resource catalog types remain
  future work

### Runtime

Implemented:

- small runtime-neutral `AgentRuntime` SPI
- custom async model/tool loop with overall/model/tool timeouts
- bounded retry/fallback behavior with error classification, backoff, jitter,
  retry budgets, and circuit breaking
- session history, optional memory auto-store, injected policy, and telemetry
- concurrency-safe service lifecycle, failure recovery, and in-flight drain on
  stop
- definition-level concurrency limit with wait/reject overload behavior and
  stop wake-up handling
- client cancellation propagation and bounded shutdown drain with cancellation
  of stuck invocation tasks
- one invocation deadline budget shared across model, tool/MCP, session, and
  memory operations; request deadlines are enforced and cancellation propagates
  into the active provider call
- complete conversation turns are persisted for session-backed invocations,
  including assistant `tool_calls` and matching tool results, so subsequent
  model requests can replay provider-required tool transcripts
- required runtime capabilities are checked at startup against an explicit
  capability matrix and are surfaced by `GET /v1/capabilities`
- configured model, session, memory, and declared MCP dependencies are probed
  before the runtime marks an agent ready; failures leave the agent non-ready
- declared input/output contracts are enforced at the core invocation boundary
- optional `runtimes/google_adk` adapter constructs Google ADK `LlmAgent`,
  `Runner`, session, and memory-service objects while keeping them behind the
  runtime SPI
- ADK adapter bridges the existing model-provider contract, native tools,
  session lifecycle, invocation deadlines, and terminal responses
- ADK adapter maps the declared memory dependency onto an ADK
  `BaseMemoryService` bridge over the Micro-Agent memory provider (including
  auto-store and search), wires telemetry spans/metrics/logs around the
  runner, enforces injected policy deterministically around every tool
  execution and declared MCP server, and exposes MCP-discovered tools as ADK
  tools through the injected MCP manager
- declared knowledge sources are health-checked at startup in both runtimes
  against the configured knowledge provider and exposed as a `knowledge`
  health probe
- declared policy references resolve through an injected policy, resolver,
  or configured strict HTTP policy store; unresolvable references fail before
  runtime creation
- optional downstream token exchange resolves a short-lived Bearer token from
  the verified invocation identity and refreshes it for each remote MCP
  request; stdio credentials remain startup-scoped by transport design
- policy enforcement covers skills and model restrictions (allow/deny model
  and provider sets) in addition to tools and MCP servers; denied declared
  skills, models, or MCP servers fail startup
- every declared credential reference (model, MCP server, security) must
  resolve through the configured credential provider before runtime creation;
  MCP connections resolve declared credentials at connect time
- caller-supplied request metadata is never used to construct caller, user,
  or workload identity; a source-level guard test enforces this boundary
- executable bootstrap selects the custom loop by default or the Google ADK
  adapter through `MICRO_AGENT_RUNTIME`; ADK session providers bridge SQLite,
  Redis, and PostgreSQL state into durable event/state transcripts, while
  native model credentials are resolved into an owned GenAI client
- declared MCP servers connect through the official SDK wire client at
  startup when the `mcp` extra is installed; without it, startup fails with
  an installation message instead of silently ignoring the declarations
- the optional Redis session provider validates `redis://`/`rediss://`
  endpoints, updates session documents and their active index in transactional
  pipelines, enforces expiry with Redis key TTLs, cleans stale index members,
  exposes a health probe, and closes only clients it owns
- the optional Redis memory provider stores scoped JSON records in a shared
  namespace, applies `MemoryPolicy` TTL/capacity retention, purges stale index
  members, exposes a health probe, and closes only clients it owns
- the optional Redis operation registry atomically claims idempotency keys,
  shares in-progress/completed results across custom-runtime replicas, applies
  result TTLs, exposes a health probe, and closes only clients it owns
- tool declarations classify side effects as `read_only`, `idempotent`, or
  `unsafe`; both runtimes enforce the classification, defaulting legacy and
  discovered tools to `unsafe`
- the custom runtime suppresses whole-invocation retries after a non-read-only
  tool starts, including failures while recording its operation result
- retry attempts are bounded by definition-level attempt and wall-clock
  budgets, with exponential backoff and optional jitter; defaults preserve one
  immediate retry
- checkpoint persistence/resume is capability-gated: both runtimes store
  replay-safe boundaries and resume explicitly by checkpoint id when a store
  is configured; the Google ADK adapter persists an ADK event/state snapshot
  and invalidates checkpoints before a pending non-read-only tool can replay
- Google ADK non-read-only tools use the configured operation registry for
  tenant-scoped atomic idempotency claims and stored-result replay

Current runtime capability matrix:

| Capability | Availability | Notes |
|---|---|---|
| `streaming` | provider-dependent | true for OpenAI-compatible and native Anthropic providers, a fake provider configured with stream chunks, and the Google ADK adapter when its injected provider advertises streaming; native ADK model selection remains conservative |
| `structured_output` | provider-dependent | true for the OpenAI-compatible provider and the Google ADK adapter when its injected provider advertises structured output; native ADK model selection remains conservative |
| `memory` | configured | true only when a memory provider is injected |
| `mcp` | configured | true only when an MCP manager is injected |
| `a2a` | transport-level | the runtime flag remains false; the official SDK transport provides non-streaming and streaming task protocols separately |
| `checkpointing` | configured | true when a checkpoint store is injected; Google ADK additionally requires its ADK resumability configuration and stores an event/state snapshot |

Gaps:

- the Google ADK adapter still uses native approval continuations, so the
  durable `MICRO_AGENT_APPROVAL_ENDPOINT` remains a custom-runtime binding

### Performance and resource budgets

Implemented:

- deterministic direct-runtime and in-process HTTP load scenarios using the
  fake model, reporting error rate, p50/p95/max latency, throughput, and peak
  traced memory
- versioned per-scenario budgets in `benchmarks/budgets.json`, with both
  scenarios enforced in the unit CI job and small-load regression tests
- an operator-invoked external harness for deployed Micro-Agent HTTP and
  Streamable HTTP MCP endpoints, measuring bounded live model/network/tool
  latency with redacted token handling
- an operator-invoked sequential rising-concurrency capacity matrix that
  preserves per-stage errors, p95 latency, throughput, replica count, and
  shared-state metadata

Gaps:

- live multi-replica contention and production SLO sign-off still require
  deployment-owned Redis/Postgres environments; the checked-in CI scenarios
  remain deterministic framework-overhead guardrails

### Models and tools

Implemented:

- deterministic fake provider
- injectable OpenAI-compatible chat-completions provider
- native Anthropic Messages provider with `tool_use`/`tool_result` translation
  and SSE streaming
- versioned `ModelCatalog` SPI with in-memory and strict HTTPS HTTP
  implementations; bootstrap resolves a logical `model.ref` before provider
  construction
- built-in `echo` and bounded side-effect-free `json_parse` tools,
  installed-package extensions through
  `micro_agent.tools` entry points, programmatic tool injection, and injected
  MCP tool adapters

Implemented (additions):

- provider tool-call IDs and the assistant `tool_calls` payload are preserved
  in the conversation history and tool results carry `tool_call_id`
- tool requests are validated against declared JSON Schema inputs before
  execution
- explicit proxy/TLS configuration and injectable HTTP clients for the
  OpenAI-compatible and Anthropic providers
- provider capability reporting with tool-use negotiation enforced at startup
- endpoint path prefixes (for example `/v1`) are preserved when constructing
  `/models` and `/chat/completions` requests, and the declared provider model ID
  is passed through the runtime
- live loopback acceptance coverage completes a multi-turn tool call and
  replays its transcript from session storage

Gaps:

- the built-in provider set currently covers fake, OpenAI-compatible chat
  completions, and Anthropic Messages; other model families require additional
  provider adapters
- bundled native tools remain intentionally domain-neutral; conceptual examples
  that declare domain tools require installed plugins or programmatic injection

### MCP

Implemented:

- interfaces and data models
- fake client and injectable connection manager
- basic TLS/origin/transport/response-size checks
- discovered tool adapter and manager health state
- declared credentials resolve through the configured credential provider at
  connect time and are passed separately from config objects
- official MCP SDK wire client (stable `2025-11-25`, optional `mcp` extra)
  selected by the bootstrap: initialization and version/capability
  negotiation, tool/resource/prompt discovery, tool invocation with per-call
  timeouts, graceful close; stdio models local command/args, Streamable HTTP
  is the standard transport, SSE is legacy compatibility only; interop tests
  run real FastMCP servers over stdio and Streamable HTTP; Streamable HTTP
  clients disable ambient proxy environment variables by default
- bounded automatic reconnect after unexpected transport termination, with
  exponential backoff, explicit shutdown suppression, and a terminal error
  state after attempts are exhausted
- application-visible bounded `McpNotification` events with async callback
  delivery and SDK-to-SPI normalization

Gaps:

- tests exercise loopback HTTP and local stdio servers, not remote
  production deployments

### A2A

Implemented:

- the official a2a-sdk serves the standard `/.well-known/agent-card.json`
  route with the SDK card model: protocol binding/version, security schemes
  advertised from the configured authenticator, input/output modalities, and
  complete skill metadata
- the official `a2a-sdk==1.0.1` JSON-RPC transport with complete non-streaming
  and streaming task lifecycles (submitted → working → completed/failed) bridged onto
  Micro-Agent invocations through an AgentExecutor; streaming artifact chunks
  are emitted only when the bound runtime advertises streaming
- cancellation of in-flight executor tasks is wired through to the runtime
  invocation and transitions the A2A task to `canceled`
- transport authentication shared with the native API guards A2A
  interactions when caller identity is required; the card advertises the
  configured OIDC scheme
- declared protocol versions are validated at startup against the versions
  the installed SDK supports, and requests declaring another version are
  rejected
- bounded tenant-scoped SQLite task persistence with expiry and JSON snapshot
  limits, plus durable push callback configuration storage
- bounded HTTPS push delivery with callback authentication, host allowlists,
  transient retry, and card capability advertisement
- official-SDK client interop tests: resolver + client resolve the card and
  complete both non-streaming and streaming tasks end-to-end

Gaps:

- the SQLite stores are a single-process reference backend; a shared
  multi-replica implementation remains deployment work
- the supported A2A v1.0.1 surface is covered by the official SDK matrix:
  discovery, non-streaming/streaming, cancellation, task get/list,
  push-configuration CRUD, not-found errors, and version rejection; optional
  extended-card features remain unadvertised


### Security and policy

Implemented:

- separate agent/caller/user/workload identity types
- transport authentication middleware behind an `Authenticator` SPI selected
  through `MICRO_AGENT_AUTH`; OIDC/OAuth2 Bearer JWT validation implemented
  first (JWKS signatures, issuer/audience/expiry, standard-claim mapping to
  caller and user/tenant identity), mapped to the stable 401 contract, with
  health/discovery routes public and fail-fast app creation when the
  definition requires caller identity without an authenticator
- verified identity travels on `AgentRequest`; caller-supplied request
  metadata is never used as identity, enforced by a source-level guard test
- approval/confirmation continuation in the built-in runtime: approval-gated
  operations pause with a continuation id and resume on approve/deny; the
  approval store is an SPI with an in-memory default and an optional durable
  Redis implementation wired through `MICRO_AGENT_APPROVAL_ENDPOINT`
- Google ADK approval continuations use the native experimental
  `ToolConfirmation` protocol: approval-gated ADK tools emit a continuation
  with pending tool metadata and resume through the original session without
  exposing ADK types through the SPI
- optional OpenTelemetry SDK integration emits standard spans and metrics
  through the existing facade, preserves deterministic in-memory test
  collectors, bridges W3C HTTP context through HTTP, model, MCP, tool, and A2A
  paths, and suppresses content attributes by default with bounded labels;
  token/cost metric conventions and a Prometheus-compatible `/metrics` route
  are documented
- durable, redacted audit events through an `AuditSink` SPI (stdout JSONL
  default, optional file and SQLite sinks) covering policy denials, approval
  decisions, and authentication failures; SQLite rows have tenant filtering
  and bounded retention
- verified identity propagates through model, tool, and MCP operations via
  an invocation-scoped context binding; workload identity resolves from
  environment overrides, the Kubernetes service-account mount, or the
  hostname
- programmatically injected allow/deny evaluator
- in-memory operation registry
- recursive log-key/known-value redaction
- declared policy references resolve through an injected policy, resolver, or
  strict configured HTTP policy store, and declared credential references
  (model, MCP, security) resolve through the configured credential provider
  before runtime creation
- skill and model-restriction enforcement alongside tool and MCP policy;
  denied declared skills, models, or MCP servers fail startup
- conditional `PolicyRule` evaluation by resource, action, and verified
  invocation identity context, including deterministic operator matching and
  deny-over-allow precedence
- strict HTTP policy-store resolution for declared references, including
  optional bearer authentication, secret-provider token bindings, HTTPS/
  loopback enforcement, no ambient proxies or redirects, and fail-closed
  response parsing

Gaps:

- the token exchange service is an optional deployment dependency; its
  endpoint contract and availability are not verified by local framework tests

### State and knowledge

Implemented:

- in-memory session and memory providers
- SQLite session provider
- optional Redis memory provider for declared shared memory and Redis session
  provider for shared `persistence: external` state
- optional Redis operation registry for custom-runtime distributed idempotency
  claims and results
- optional PostgreSQL state providers (`postgres` extra, asyncpg): a
  concurrency-safe session provider and a tenant/scope-partitioned memory
  provider, both with optimistic version-based conflict detection, plus a
  PostgreSQL operation registry with atomic cross-process idempotency-key
  claims; `postgres://`/`postgresql://` session, memory, and idempotency
  endpoints select them at bootstrap, concurrent DDL is serialized with
  advisory locks, and pools close on shutdown
- `MemoryPolicy` validates retention bounds; expired in-memory entries are
  purged before reads, writes, and capacity eviction so stale entries cannot
  consume capacity or evict live data
- SQLite operations use an explicit per-provider async lock and bounded
  SQLite busy timeout; the provider is documented and tested as a
  single-process development store
- in-memory and durable tenant-scoped SQLite keyword knowledge retrievers,
  with versioned documents, deterministic retrieval, and startup health checks
  in both runtimes
- bounded `HttpKnowledgeRetriever` adapter for semantic/vector or hybrid
  search services, with HTTPS/loopback endpoint validation, no ambient proxy or
  redirects, optional bearer auth, tenant/version propagation, strict result
  parsing, content hashes, and readiness probing

Gaps:

- SQLite is a development persistence example, not a Kubernetes multi-replica
  external store; PostgreSQL providers require the optional extra and a
  provisioned database (not embedded with the framework)
- SQLite knowledge, task, and audit stores are portable single-process
  reference backends, not Kubernetes multi-replica shared services; the HTTP
  knowledge adapter does not include or operate the distributed vector/index
  service itself;
  PostgreSQL providers require the optional extra and a provisioned database
- state providers scope records by verified tenant when available and reject
  stale non-zero-version updates; unscoped zero-version writes remain a
  compatibility path

### HTTP, health, and observability

Implemented:

- FastAPI invoke, liveness, readiness, capability, and official A2A card/task
  routes
- active injected dependency probes
- generated HTTP request IDs and non-success unhealthy readiness
- input/output contract checks at the core boundary and HTTP 422 diagnostics
- concurrency overload mapped to HTTP 429 with retry guidance
- request/definition deadline exhaustion mapped to HTTP 504 with a stable
  `deadline_exceeded` code
- authorization, dependency, and unexpected runtime failures mapped to stable
  HTTP 403/503/500 contracts
- authentication middleware: unauthenticated calls to `/v1/invoke` receive
  the stable 401 `authentication_required` contract with `WWW-Authenticate:
  Bearer` before the agent is reached; health and discovery routes stay
  public
- the reference cloud gateway supports the same asymmetric OIDC JWT policy
  through its synchronous `OidcGatewayAuthenticator`, in addition to static
  bearer grants for local deployments
- unknown/expired approval continuations map to a stable 404
  `continuation_not_found` contract; `approval_required` responses carry a
  continuation id and pending tool names
- configurable fixed-length and chunked request-size guard (1 MiB default)
- versioned `/v1/openapi.json`, `/v1/docs`, and `/v1/redoc` routes with an
  `X-Micro-Agent-API-Version` response header; `/openapi.json` remains a
  compatibility alias
- opt-in CORS allowlists from `create_app()` or `MICRO_AGENT_CORS_ORIGINS`,
  with credentials disabled by default
- bounded process-local token-bucket limiter plus injectable
  synchronous/asynchronous `RateLimiter` hook with stable 429/503 contracts
  and retry/rate-limit headers
- the Cloud gateway supports an injectable shared-state SPI; the optional
  `RedisGatewayStateStore` atomically coordinates per-route rate limits,
  per-target circuit transitions, and expiring bulkhead leases across workers
- event-stream response pass-through for gateway calls requesting
  `text/event-stream`, with bulkhead ownership held through stream completion
- streaming negotiation rejects `text/event-stream` when the selected runtime
  does not advertise streaming; no unsupported stream is claimed
- response streaming is implemented for the built-in and Google ADK runtimes
  when their model providers advertise it, and both HTTP and A2A transports
  forward those chunks
- structured logger, in-memory metrics and bounded histograms, and in-memory
  span tree

Gaps:

- dashboards and alert thresholds remain deployment-owned; the full metric
  inventory, recommended dashboard panels, and PromQL alert examples are
  documented in docs/OBSERVABILITY.md (guarded by a source-vs-docs test), and
  `/metrics` exposes the in-memory operational series while the OTel SDK stays
  configurable through standard exporter/provider settings

### Packaging, release, and deployment

Implemented:

- build metadata, Dockerfile, sample manifests
- CI jobs for tests, schema, package/container smoke, separate dependency
  audits, SBOM, and strict docs
- tag-triggered, quality-gated PyPI/GHCR/GitHub release workflow
- package metadata and `micro-agent` console entrypoint
- hash-pinned Linux/Python 3.11 runtime requirements with `pip --require-hashes`
  installation in the Dockerfile
- generated v1alpha1 cloud descriptor, config-record, and observability-batch
  schemas with matching boundary validation and compatibility documentation
- strict Vault KV v2 credential resolution with fresh lookups and safe
  endpoint/response handling
- optional AWS Secrets Manager credential resolution with fresh lookups,
  plain or JSON-field secret references, UTF-8 binary support, strict response
  handling, and redacted error/repr surfaces
- checked-in Kubernetes baseline with resource requests/limits, replica
  spreading, disruption budget, autoscaling, default-deny network policy,
  Prometheus scrape annotations, and a tested 25-second drain/30-second grace
  shutdown policy
- optional Redis-backed A2A task and push-configuration stores selected by a
  `redis://`/`rediss://` A2A store location, with tenant-scoped task keys,
  bounded snapshots, TTLs, and shared-worker integration coverage

Gaps:

- PyPI trusted publishing must be configured before the first tag
- provider-specific NetworkPolicy selectors and target-cluster OpenShift
  SecurityContextConstraints validation still require the target cluster
- production-cluster immutable digest/signature admission and live rollback
  promotion remain deployment-owner work; the release gate now signs and
  verifies the exact image digest, SBOM attestation, SLSA provenance, and
  compatibility fixtures before publication

## Cloud workstream (C0–C5 reference durability)

Started 2026-09-03 as an explicitly scoped reference effort ahead of the PyPI
release-gate item. C0 defined the control-plane boundary (ADR 0013 +
CLOUD_ARCHITECTURE.md). C1 implemented the minimal registry/discovery slice
(ADR 0014 + CLOUD_REGISTRY.md); C2 added the versioned configuration plane
(ADR 0015 + CLOUD_CONFIG.md); C3 added the gateway and resilience set
(ADR 0016 + CLOUD_GATEWAY.md); and C4 added cross-agent observability
(ADR 0017 + CLOUD_OBSERVABILITY.md). C5 now adds restart-safe SQLite reference
stores for the registry, config plane, and observability aggregation, with
retention/lease handling; gateway state defaults to per-process but supports
shared Redis rate/circuit/bulkhead coordination while event-stream responses
pass through without buffering; plane APIs now support explicit
static/OIDC authentication while retaining an unauthenticated local default.
The `cloud`
package is not part of the published `micro-agents` distribution. The core
framework neither imports nor depends on cloud code; standalone product claims
are unchanged. [ADR 0018](adr/0018-cloud-repository-boundary.md) records the
repository-boundary evaluation: keep the reference slices in this repository
until their contracts, ownership, release automation, and CI boundary
stabilize, then revisit an independent split.

## Production-readiness conclusion

The repository is a credible architecture prototype and contract testbed. Its
strongest artifacts are the runtime-neutral definition, SPI, focused provider
interfaces, and deterministic tests. Its primary risk is documentation that
previously promoted injected seams and fake-client tests as end-to-end
production capabilities.

The immediate release-gate action is the PyPI trusted-publisher configuration
(an owner action on pypi.org). Remaining implementation and deployment
priorities are production execution of the capacity matrix, distributed
knowledge-service operations, target-cluster supply-chain admission, and live
promotion/rollback; the complete prioritized backlog is in
[`TODO.md`](https://github.com/bassemZohdy/micro-agents/blob/main/TODO.md).
