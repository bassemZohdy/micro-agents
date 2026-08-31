# Implementation Status

Last audited: 2026-08-31  
Documentation-audit baseline: `87d1779eb54878ac73cea7730694d25b83400882`
Cleanup verification baseline: `bd077fa005c2c5b5d12f9020b57d36121f09fa4d`

This document separates implemented code from architectural intent. Passing
unit tests prove the exercised behavior only; they do not establish production
readiness or protocol compliance.

## Verification snapshot

| Check | Result | Evidence/qualification |
|---|---|---|
| Ruff lint and format | Pass | local and remote CI |
| Tests | 466 collected | 397 selected by the default test job (including the fourteen optional Google ADK adapter tests and the authentication, audit, propagation, and MCP SDK interop tests); the remaining 69 run in the integration/e2e jobs |
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

Gaps:

- the bootstrap resolves model provider, endpoint, model ID, and credentials;
  built-in memory and SQLite/in-memory session bindings, the built-in tool
  registry, the MCP connection manager, the knowledge provider, the
  credential provider, and telemetry are constructed from configuration;
  external state providers remain unsupported and fail fast
- model aliases and provider model IDs are separate fields; a versioned
  resource/catalog contract is still needed for alias resolution

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

Gaps:

- broader provider credentials and endpoints are not yet supported
- OpenAI-compatible follow-up messages omit assistant tool-call structures and
  tool-call IDs
- only `echo` resolves from the built-in map; the residency example's native
  tools remain unresolved
- no input/output JSON Schema validation around tool calls

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
  run real FastMCP servers over stdio and Streamable HTTP

Gaps:

- no automatic reconnect for dropped server connections; reconnection
  currently requires a runtime restart
- notifications are consumed by the SDK session but not surfaced as events
- tests exercise loopback HTTP and local stdio servers, not remote
  production deployments

### A2A

Implemented:

- project-local dataclasses
- definition-to-card mapping
- one discovery endpoint

Gaps:

- current route is `/.well-known/agent.json`, while A2A v1 uses
  `/.well-known/agent-card.json`
- current card shape is pre-v1/custom and lacks `supportedInterfaces`
- no standard message/task server binding
- the “independent client” test checks raw JSON with project-authored
  expectations; it is not official-SDK compatibility

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

- the Google ADK adapter still converts `approval_required` into a denial;
  mapping continuation onto ADK's native tool confirmation was probed and is
  non-trivial: the pinned ADK marks the feature experimental, its runner
  continues after a tool requests confirmation (only the tool is gated), and
  a synthetic confirmation function response does not re-execute the original
  tool without deeper integration into the experimental protocol
- downstream delegation (for example token exchange toward MCP servers) is
  not implemented; propagation currently makes the verified principal
  observable to operations, and per-protocol delegation arrives with the
  official MCP/A2A integrations
- generic `PolicyRule` conditions are not evaluated
- the audit sink persists to the platform log pipeline or a local file;
  database-backed audit arrives with production state providers
- the approval store is process-local; production approval state arrives
  with production state providers
- idempotency storage is process-local and non-atomic

### State and knowledge

Implemented:

- in-memory session and memory providers
- SQLite session provider
- in-memory keyword knowledge retriever, constructed from declared knowledge
  sources and health-checked at startup in both runtimes

Gaps:

- bootstrap only constructs in-memory memory and in-memory/SQLite session
  providers; external state endpoints are rejected until a provider is wired
- SQLite is a development persistence example, not a Kubernetes multi-replica
  external store
- SQLite access lacks explicit async serialization around one connection
- expired memory entries are skipped but not consistently purged
- no production memory, knowledge, session, or idempotency provider

### HTTP, health, and observability

Implemented:

- FastAPI invoke, liveness, readiness, capability, and preliminary card routes
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
- structured logger, in-memory metrics, and in-memory span tree

Gaps:

- no response streaming
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
