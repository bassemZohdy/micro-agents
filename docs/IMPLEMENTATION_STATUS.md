# Implementation Status

Last audited: 2026-09-01
Documentation-audit baseline: `a2f0e64c71fc7bbddf4ed9ac961c11bf5da01bf4`
Cleanup verification baseline: `a2f0e64c71fc7bbddf4ed9ac961c11bf5da01bf4`

This document separates implemented code from architectural intent. Passing
unit tests prove the exercised behavior only; they do not establish production
readiness or protocol compliance.

## Verification snapshot

| Check | Result | Evidence/qualification |
|---|---|---|
| Ruff lint and format | Pass | local and remote CI |
| Tests | 507 base collected | 429 selected by the default test job (including Redis memory/session/idempotency-provider unit coverage, reconnect, authentication, audit, propagation, HTTP policy hooks, MCP SDK interop, and wire-protocol tests); 78 baseline integration tests run in the integration job, with five also tagged E2E, plus three Redis service tests and 15 Google ADK tests when optional extras are installed |
| Schema drift | Pass | generated schema matches the tracked file |
| Container smoke | Pass | fake-provider startup and three HTTP endpoints |
| Package build | Pass | wheel/sdist build plus isolated wheel import and console-entrypoint smoke |
| Documentation | Pass | strict MkDocs build on pull requests; publish only from `main` |
| Strict type check | Pass | `types-PyYAML` is part of the development extra |
| Dependency audit | Pass | runtime and development environments are audited separately |
| Overall GitHub CI | Required | see the [latest main workflow](https://github.com/bassemZohdy/micro-agents/actions/workflows/ci.yml?query=branch%3Amain) |

The OpenAI-compatible client defaults to direct connections (`trust_env=False`)
so ambient proxy variables cannot unexpectedly route model traffic or loopback
tests. Deployments that require a proxy must opt in through provider
configuration; proxy policy remains part of production hardening.

## Capability assessment

### Definition and configuration

Implemented:

- strict Pydantic `microagents.io/v1alpha1` model
- YAML loader with diagnostics
- generated draft-2020-12 JSON Schema and CI drift check
- semantic validation for names, versions, references, transports, URLs,
  scopes, capabilities, and duplicate collections
- runtime-neutral input/output contract enforcement with stable diagnostics
- a separate `resolve_config()` precedence utility
- typed deployment-only `EnvironmentOverlay` endpoint bindings for model, MCP,
  memory, and session services; bindings are validated and applied without
  mutating the logical definition
- a canonical `v1alpha1` compatibility fixture and migration guidance; the
  loader continues to reject unsupported API versions and unknown fields

Gaps:

- the bootstrap resolves model provider, endpoint, model ID, and credentials;
  built-in memory and SQLite/in-memory session bindings plus optional Redis
  external memory/session bindings, the built-in tool
  registry, the MCP connection manager, the knowledge provider, the
  credential provider, telemetry, and the custom runtime's optional Redis
  operation registry are constructed from configuration; unsupported external
  schemes fail fast and the Google ADK runtime rejects the binding until mapped
- model aliases and provider model IDs are separate fields; a versioned
  resource/catalog contract is still needed for alias resolution
- only `microagents.io/v1alpha1` is currently supported; a future API version
  needs a separate model, schema, compatibility fixture, and migration policy

### Runtime

Implemented:

- small runtime-neutral `AgentRuntime` SPI
- custom async model/tool loop with overall/model/tool timeouts
- one retry or fallback behavior
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
- declared policy references resolve through an injected policy or a
  configured policy resolver; unresolvable references fail before runtime
  creation
- policy enforcement covers skills and model restrictions (allow/deny model
  and provider sets) in addition to tools and MCP servers; denied declared
  skills, models, or MCP servers fail startup
- every declared credential reference (model, MCP server, security) must
  resolve through the configured credential provider before runtime creation;
  MCP connections resolve declared credentials at connect time
- caller-supplied request metadata is never used to construct caller, user,
  or workload identity; a source-level guard test enforces this boundary
- executable bootstrap selects the custom loop by default or the Google ADK
  adapter through `MICRO_AGENT_RUNTIME`; ADK declarations that still cannot
  be mapped (external session state, model credential references) fail fast
  rather than being silently ignored
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

Current custom-runtime capability matrix:

| Capability | Availability | Notes |
|---|---|---|
| `streaming` | false | streaming is not implemented |
| `structured_output` | false | declared contracts are validated, but model structured-output APIs are not wired |
| `memory` | configured | true only when a memory provider is injected |
| `mcp` | configured | true only when an MCP manager is injected |
| `a2a` | false | discovery is preliminary and task protocol is not implemented |
| `checkpointing` | false | checkpoint persistence is not implemented |

Gaps:

- policy references cannot yet resolve from external policy *stores*; the
  bootstrap accepts an injected policy or a policy resolver callable, and
  fails fast when neither can satisfy a declared reference
- retrying the complete invocation can replay side effects

### Models and tools

Implemented:

- deterministic fake provider
- injectable OpenAI-compatible chat-completions provider
- built-in `echo` tool and injected MCP tool adapters

Implemented (additions):

- provider tool-call IDs and the assistant `tool_calls` payload are preserved
  in the conversation history and tool results carry `tool_call_id`
- tool requests are validated against declared JSON Schema inputs before
  execution
- explicit proxy/TLS configuration and injectable HTTP clients for the
  OpenAI-compatible provider
- provider capability reporting with tool-use negotiation enforced at startup
- endpoint path prefixes (for example `/v1`) are preserved when constructing
  `/models` and `/chat/completions` requests, and the declared provider model ID
  is passed through the runtime
- live loopback acceptance coverage completes a multi-turn tool call and
  replays its transcript from session storage

Gaps:

- broader provider credentials and endpoints are not yet supported
- only `echo` resolves from the built-in map; the residency example's native
  tools remain unresolved

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

Gaps:

- notifications are consumed by the SDK session but not surfaced as events
- tests exercise loopback HTTP and local stdio servers, not remote
  production deployments

### A2A

Implemented:

- the official a2a-sdk serves the standard `/.well-known/agent-card.json`
  route with the SDK card model: protocol binding/version, security schemes
  advertised from the configured authenticator, input/output modalities, and
  complete skill metadata
- the JSON-RPC transport with a complete non-streaming task lifecycle
  (submitted → working → completed/failed) bridged onto Micro-Agent
  invocations through an AgentExecutor
- transport authentication shared with the native API guards A2A
  interactions when caller identity is required; the card advertises the
  configured OIDC scheme
- declared protocol versions are validated at startup against the versions
  the installed SDK supports, and requests declaring another version are
  rejected
- official-SDK client interop test: resolver + client resolve the card and
  complete a non-streaming task end-to-end

Gaps:

- streaming tasks, push notifications, and extended authenticated cards are
  not implemented (card advertises streaming as unavailable)
- task store is in-memory; durable task state arrives with production state
  providers
- cancellation of in-flight invocations is not wired to the A2A task
  cancellation path


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
  approval store is an SPI with an in-memory default
- Google ADK approval continuations use the native experimental
  `ToolConfirmation` protocol: approval-gated ADK tools emit a continuation
  with pending tool metadata and resume through the original session without
  exposing ADK types through the SPI
- durable, redacted audit events through an `AuditSink` SPI (stdout JSONL
  default, optional file sink) covering policy denials, approval decisions,
  and authentication failures
- verified identity propagates through model, tool, and MCP operations via
  an invocation-scoped context binding; workload identity resolves from
  environment overrides, the Kubernetes service-account mount, or the
  hostname
- programmatically injected allow/deny evaluator
- in-memory operation registry
- recursive log-key/known-value redaction
- declared policy references resolve through an injected policy or policy
  resolver, and declared credential references (model, MCP, security) resolve
  through the configured credential provider before runtime creation
- skill and model-restriction enforcement alongside tool and MCP policy;
  denied declared skills, models, or MCP servers fail startup

Gaps:

- downstream delegation (for example token exchange toward MCP servers) is
  not implemented; propagation currently makes the verified principal
  observable to operations, and per-protocol delegation arrives with the
  official MCP/A2A integrations
- generic `PolicyRule` conditions are not evaluated
- the audit sink persists to the platform log pipeline or a local file;
  database-backed audit arrives with production state providers
- the approval store is process-local; production approval state arrives
  with production state providers
- Redis operation idempotency is available in the custom runtime, with claims
  scoped by verified tenant when present; late completion is owner-checked and
  the Google ADK adapter rejects the binding

### State and knowledge

Implemented:

- in-memory session and memory providers
- SQLite session provider
- optional Redis memory provider for declared shared memory and Redis session
  provider for shared `persistence: external` state
- optional Redis operation registry for custom-runtime distributed idempotency
  claims and results
- `MemoryPolicy` validates retention bounds; expired in-memory entries are
  purged before reads, writes, and capacity eviction so stale entries cannot
  consume capacity or evict live data
- SQLite operations use an explicit per-provider async lock and bounded
  SQLite busy timeout; the provider is documented and tested as a
  single-process development store
- in-memory keyword knowledge retriever, constructed from declared knowledge
  sources and health-checked at startup in both runtimes

Gaps:

- bootstrap constructs in-memory or Redis memory, in-memory/SQLite sessions,
  Redis external sessions, and the custom runtime's Redis operation registry;
  unsupported idempotency schemes are rejected
- SQLite is a development persistence example, not a Kubernetes multi-replica
  external store
- no production knowledge provider; state providers scope records by verified
  tenant when available and reject stale non-zero-version updates; unscoped
  zero-version writes remain a compatibility path

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
- unknown/expired approval continuations map to a stable 404
  `continuation_not_found` contract; `approval_required` responses carry a
  continuation id and pending tool names
- configurable `Content-Length` request-size guard (1 MiB default)
- versioned `/v1/openapi.json`, `/v1/docs`, and `/v1/redoc` routes with an
  `X-Micro-Agent-API-Version` response header; `/openapi.json` remains a
  compatibility alias
- opt-in CORS allowlists from `create_app()` or `MICRO_AGENT_CORS_ORIGINS`,
  with credentials disabled by default
- injected synchronous/asynchronous `RateLimiter` hook with stable 429/503
  contracts and retry/rate-limit headers
- streaming negotiation rejects `text/event-stream` when the selected runtime
  does not advertise streaming; no unsupported stream is claimed
- structured logger, in-memory metrics, and in-memory span tree

Gaps:

- no response streaming implementation; runtimes currently advertise
  `streaming: false`
- telemetry is not OpenTelemetry and does not propagate standard trace context

### Packaging, release, and deployment

Implemented:

- build metadata, Dockerfile, sample manifests
- CI jobs for tests, schema, package/container smoke, separate dependency
  audits, SBOM, and strict docs
- tag-triggered, quality-gated PyPI/GHCR/GitHub release workflow
- package metadata and `micro-agent` console entrypoint

Gaps:

- release does not yet validate schema/image/changelog alignment
- PyPI trusted publishing must be configured before the first tag
- repository rulesets still need to make all main/release checks mandatory
- fixed UID assumptions are not OpenShift arbitrary-UID friendly
- sample Secret contains an empty value and no secret-manager workflow
- deployment image uses `latest` and the example declares integrations that
  bootstrap does not wire

## Production-readiness conclusion

The repository is a credible architecture prototype and contract testbed. Its
strongest artifacts are the runtime-neutral definition, SPI, focused provider
interfaces, and deterministic tests. Its primary risk is documentation that
previously promoted injected seams and fake-client tests as end-to-end
production capabilities.

The next implementation sequence is P0.1 through P0.4 in
[`TODO.md`](https://github.com/bassemZohdy/micro-agents/blob/main/TODO.md),
followed by A2A/MCP/provider interoperability and external state.
