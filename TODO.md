# Micro-Agents — TODO

This document contains the implementation and architecture backlog for the Micro-Agents project.

Do not mark tasks complete until implementation, tests, and relevant documentation are complete.

---

# Review Findings — 2026-08-30 (verified)

A full review found that most runtime behaviour is **interface-only**: dataclasses,
ABCs, and one fake model. Tests pass (222), `ruff`/`mypy` are clean, coverage is 98%,
but the tests almost exclusively construct dataclasses or assert that an ABC is
abstract — they do not exercise the milestone "Acceptance" criteria.

**Re-verification (2026-08-30, commit `75f3608`):** every finding in sections A–P
below was re-checked against the current code and confirmed. None have been
addressed. The milestone checkboxes contradicted by these findings have been
un-checked in the sections that follow; the remaining `[x]` items are genuine
(interface/model/docs work that does exist).

## A. The agent cannot actually run

- [x] **No process entrypoint.** `Dockerfile` runs `python -m micro_agent`, but
  `micro_agent/__init__.py` is empty and there is no `micro_agent/__main__.py`.
  The container fails at start with `No module named micro_agent.__main__`.
- [x] **No HTTP server.** `micro_agent/interoperability/http_api.py` only defines
  request/response dataclasses, a `ROUTES` dict of strings, and `serialize_response`.
  There is no ASGI/FastAPI app, no handlers for `POST /v1/invoke`, `GET /health/live`,
  `GET /health/ready`, `GET /v1/capabilities`. `pyproject.toml` has no web-framework
  dependency. Milestone 15 acceptance is not met.
- [x] **Docker `HEALTHCHECK`** targets `http://localhost:8080/health/live`, which can
  never respond because there is no server.
- [x] `serialize_response` (`http_api.py:72`) does `json.dumps(data.__dict__, default=str)`,
  so nested dataclasses/enums are stringified into Python reprs, not JSON. E.g. a
  `ReadinessResult` with dependencies serializes each `DependencyHealth` as an opaque
  `"DependencyHealth(name='model', status=<HealthStatus.HEALTHY: 'healthy'>, ...)"`
  string. Needs recursive/`asdict` serialization (or pydantic models).
- [x] **No concrete `MicroAgent`.** `micro_agent/core/agent.py` defines the `MicroAgent`
  ABC and `AgentState` enum, but nothing implements them. Nothing binds a
  `MicroAgentDefinition` + `AgentRuntime` into a lifecycle-managed agent
  (CREATED → INITIALIZED → STARTING → READY → RUNNING → STOPPING → STOPPED).
  `AgentState` is unused outside its own test.
- [x] **`micro_agent/lifecycle/` is an empty package** (0-byte `__init__.py`). The
  lifecycle orchestration described in Milestone 5 does not exist.
- [x] **The runtime is not distributable.** `pyproject.toml` packages only
  `["micro_agent"]`; `runtimes/` is excluded from the wheel. After `pip install .`
  (what the Dockerfile does) `import runtimes.adk` fails. The only runtime is
  unreachable from an installed distribution.

## B. ADK runtime is a stub (Milestone 14)

- [ ] `runtimes/adk/runtime.py` does not use Google ADK — no `google-adk` dependency,
  no ADK agent construction, no model binding. It only calls `FakeModelProvider`.
- [ ] `AdkRuntime.invoke` ignores tools (native + MCP), sessions, memory, skills
  metadata, knowledge, policy, and all of `RuntimeSemantics` (`timeout_seconds`,
  `max_iterations`, `error_policy`). The M14 checkboxes for Native tools / MCP tools /
  Session integration / Memory integration / Skills metadata are not implemented.
- [ ] `AdkRuntime.start` / `stop` are no-ops (`pass`). Runtime lifecycle and graceful
  shutdown are not implemented beyond nulling `_internal`.
- [ ] No real model provider. `ModelRef.provider` / `endpoint` / `credential_ref`
  (Milestone 7) are parsed but never used to construct a real client; the fake model
  is always used.
- [ ] `runtimes/adk/runtime.py:80` builds the user message as `str(request.input)`,
  producing a Python dict repr (single quotes, `None`/`True`) rather than JSON. Harmless
  with the fake model; degrades any real provider's parsing.

## C. MCP is interface-only (Milestone 9)

- [ ] `micro_agent/mcp/mcp.py` defines only the `McpClient` ABC + dataclasses. There is
  no concrete client and no in-memory/fake test double. Connect, discover tools,
  preserve resources/prompts metadata, expose allowed tools, handle connection
  failures, graceful shutdown — none are implemented.
- [ ] No official MCP SDK dependency (README "Technology" promises one).
- [ ] MCP security items are absent: TLS validation, credential redaction, endpoint
  validation, response size limits.
- [ ] Nothing consumes `McpServerRef` from a definition; M9 acceptance ("attach MCP
  through configuration only") is not demonstrable.

## D. A2A is dataclass-only (Milestone 21)

- [ ] `micro_agent/interoperability/a2a.py` defines only dataclasses. There is no
  function mapping a `MicroAgentDefinition` → `AgentCard`, no skills-mapping logic,
  no agent-card endpoint (`/.well-known/...`), no A2A server, no A2A client, no
  invocation path.
- [ ] No A2A SDK/protocol dependency (README promises "supported standard SDK/protocol").
- [ ] M21 "Test with compatible independent client" does not exist — `test_a2a.py`
  only constructs dataclasses.

## E. Observability is a custom mini-implementation, not OpenTelemetry (Milestone 17)

- [ ] `micro_agent/observability/telemetry.py` is a home-grown `StructuredLogger`,
  `MetricsCollector` (in-memory list), and `TraceSpan` (plain dataclass). No
  `opentelemetry-*` dependency, no tracer/meter providers, no OTLP exporter, no
  context propagation, no span hierarchy (agent/model/tool/MCP/memory spans).
- [ ] No instrumentation is wired into the invocation path. `AdkRuntime.invoke` emits
  no logs, metrics, or spans. The M17 metrics (invocation count/latency, errors,
  model latency, tokens, tool calls, MCP calls, memory operations) are never recorded.
- [ ] M17 acceptance ("one invocation traced through model/tool/MCP operations") is
  not met.
- [ ] `StructuredLogger` performs no secret redaction despite the M17 checkbox.

## F. Health checks are passive (Milestone 16)

- [ ] `HealthChecker.add_dependency` only stores a caller-supplied status. There are no
  active probes — nothing calls `ModelProvider.health_check()`, checks MCP
  connectivity, or checks the session/memory providers.
- [ ] `check_liveness()` always returns `alive=True`. A dependency's status cannot be
  updated after registration (list is append-only). M16 acceptance is only
  reachable by manually injecting statuses in a test.

## G. Session / Memory / Knowledge gaps

- [ ] `InMemorySessionProvider` never sets `created_at`, never sets or enforces
  `expires_at`, and `get()` does not check expiration (contradicting the ABC
  docstring). Milestone 11 "Session lifecycle" / "Expiration" unimplemented.
- [ ] No persistent session provider or reference implementation (only in-memory).
  M11 acceptance ("multiple replicas share persistent session state") is not
  demonstrable.
- [ ] `InMemoryMemoryProvider` does not enforce `MemoryPolicy` (`max_entries`,
  `ttl_seconds`, `auto_store`). Milestone 12 "Retention" unimplemented. No
  persistent memory reference implementation.
- [ ] Knowledge (Milestone 13) has no concrete `KnowledgeRetriever` implementation —
  not even a test double. No content hash / integrity metadata beyond a free-text
  `version` string.

## H. Policy / identity / side-effects are not integrated (Milestones 18–20)

- [ ] `PolicyEvaluator` is never invoked by the runtime or core. Policy is not enforced
  before tool/MCP/side-effect execution. M19 "Runtime enforcement" and its acceptance
  ("prompt injection cannot override deterministic platform policy") are only
  unit-tested in isolation.
- [ ] Nothing loads `security.policy_refs` / `security.credential_refs` /
  `security.identity_requirements` from a definition into a `SecurityContext` /
  `AgentPolicy`. M18/M19 wiring is missing.
- [ ] `OperationRegistry` / `Operation` (Milestone 20) are used by no execution path;
  idempotency/deduplication is never actually applied to a side effect.
- [ ] Module placement: `identity.py`, `policy.py`, `side_effects.py`, `health.py` live
  under `micro_agent/observability/` though they are not observability concerns.
  Consider `micro_agent/security/`, `micro_agent/health/`, `micro_agent/lifecycle/`.

## I. Tools and model timeouts (Milestones 7–8)

- [ ] No tool executor. `ToolMetadata.timeout_seconds` / `ToolDefinition.timeout_seconds`
  are never enforced.
- [ ] M8 observability items (tool invocation tracing, latency, error metrics) are not
  implemented; nothing wraps `Tool.execute`.
- [ ] `ModelConfig.timeout_seconds` (M7) is never enforced.

## J. Definition and JSON Schema (Milestone 3)

- [x] **Published JSON Schema is stale.** `docs/schemas/micro-agent-v1alpha1.json` uses
  property name `api_version` (title "Api Version"), but the model uses alias
  `apiVersion`. Regenerating via `python -m micro_agent.definition.schema` produces a
  different file. External validators using the published schema reject the repo's
  own examples (`examples/*.yaml` use `apiVersion`). Breaks M3 acceptance
  ("consumable by another runtime").
- [x] Fix: `schema.py` should call `model_json_schema(by_alias=True)` and CI should
  regenerate and `git diff --exit-code` the committed file (output also drifts across
  pydantic 2.x minor versions).
- [ ] `RuntimeSemantics` (`timeout_seconds`, `max_iterations`, `error_policy`,
  `capabilities`) is defined but honoured by no runtime.
- [x] `load_definition_from_file` (`loader.py:62`) guards only `path.exists()`; a
  directory path passes the guard and `path.read_text()` raises an unwrapped
  `IsADirectoryError` instead of `DefinitionError`, breaking the module's error contract.

## K. Configuration (Milestone 4)

- [x] `resolve_config` reads only two env vars (`MICRO_AGENT_MODEL_ENDPOINT`,
  `MICRO_AGENT_LOG_LEVEL`). No generic override for the other resolved fields
  (provider, timeout, MCP endpoints, memory/session endpoints).
- [x] Precedence bug (`config.py:113`): passing any `EnvironmentConfig` unconditionally
  overwrites `log_level` with its default `"INFO"`, clobbering a definition-supplied
  value — "unset" is indistinguishable from the default.
- [x] `MICRO_AGENT_MODEL_API_KEY` (referenced by `deploy/kubernetes/secret.yaml`) is
  not read by the config layer and never reaches `ResolvedConfig.model_api_key`.

## L. Kubernetes / container (Milestones 22–23)

- [x] `deploy/kubernetes/deployment.yaml` mounts `configMap: micro-agent-definition`,
  which does not exist — only `micro-agent-config` (holding `log-level`) is defined.
  No ConfigMap carries an agent YAML definition. The Deployment cannot schedule.
- [x] `readOnlyRootFilesystem: true` with no writable `emptyDir` for `/tmp` (M22
  "External writable paths" not represented).
- [x] No SIGTERM handling anywhere (no server/process to receive it). M22 "Graceful
  SIGTERM" unimplemented.
- [x] `Dockerfile` copies `runtimes/` but `pip install .` does not include it (see A).

## M. CI/CD and release (Milestone 26) — mostly not done

- [ ] `.github/workflows/ci.yml` runs only lint / typecheck / test / `pip-audit`.
  Missing: integration tests, E2E tests, container build + smoke test, SBOM
  generation, release versioning/tagging, container image publishing, release-notes
  automation, documentation publishing — all marked `[x]` in M26.
- [ ] `mypy` excludes `runtimes/` (pyproject) and CI runs `mypy micro_agent` only —
  the ADK runtime is never type-checked.
- [ ] No `CHANGELOG.md` despite M26 "Release notes" and a semantic-versioning workflow.
- [ ] No `.env.example` documenting the `MICRO_AGENT_*` variables.
- [ ] `docs/adr/` holds only `.gitkeep`. No ADRs record significant decisions
  (runtime-neutral definition, ADK-first, custom telemetry, observability module
  layout) although `CONTRIBUTING.md` directs contributors to ADRs.
- [ ] No docs site (mkdocs/sphinx) or Pages workflow for "Documentation publishing".

## N. Duplication and consistency

- [x] `AgentIdentity` is defined twice — `micro_agent/core/agent.py` and
  `micro_agent/observability/identity.py` (identical fields).
- [ ] `SkillDefinition` is defined twice — `micro_agent/definition/models.py` (pydantic)
  and `micro_agent/skills/skill.py` (dataclass).
- [ ] `A2AConfig` is defined twice — `micro_agent/definition/models.py` and
  `micro_agent/interoperability/a2a.py` (diverging: the latter adds `security`).
- [ ] Three near-identical skill shapes (`AgentSkill`, two `SkillDefinition`s) with no
  shared source or conversion helpers.
- [x] `micro_agent/__init__.py` is empty — no `__version__` (single source of truth vs
  `pyproject.toml`), no package docstring.
- [x] No `micro_agent/py.typed` marker despite `mypy` strict mode — downstream
  consumers get no type information.

## O. Test depth

- [ ] Add tests that exercise milestone acceptance criteria rather than dataclass
  construction: real network service, MCP-by-configuration, multi-replica shared
  session, end-to-end trace, A2A independent client, alive-but-not-ready.
- [ ] `test_architecture_validation.py` "MCP integration" / "observability" /
  "containerization" / "kubernetes" assert only YAML fields or file existence, not
  behaviour. Replace with behavioural checks once B–F land.

## P. Working-tree hygiene

- [x] Two malformed permission-allow entries in `.commandcode/settings.json`
  (lines 13–14) are entire flattened `git commit -F - <<'EOF' … EOF` invocations
  with the multi-line commit message collapsed onto one line. They can never match
  a future command, grant nothing, and bloat the allowlist. Remove both.

---

# Milestone 0 — Project Foundation

## Repository

- [x] Create repository.
- [x] Add project license.
- [x] Add contribution guide.
- [x] Add `README.md`.
- [x] Add `PROJECT_DEFINITION.md`.
- [x] Add `TODO.md`.
- [x] Add ADR directory.

## Structure

Create:

```text
docs/
  architecture/

micro_agent/
  definition/
  core/
  runtime/
  config/
  lifecycle/
  models/
  tools/
  mcp/
  skills/
  memory/
  session/
  interoperability/
  observability/

runtimes/
  adk/

examples/

tests/
```

## Development tooling

- [x] Configure Python package management.
- [x] Configure formatting.
- [x] Configure linting.
- [x] Configure static typing.
- [x] Configure unit testing.
- [x] Configure CI.
- [x] Add dependency/security scanning baseline.

## Acceptance

- [x] Project installs successfully.
- [x] CI passes.
- [x] Package modules import successfully.

---

# Milestone 1 — Define Micro-Agent Architecture

Before significant runtime implementation, formalize the architecture.

## Definition

- [x] Define Micro-Agent.
- [x] Define bounded agentic capability.
- [x] Define independent deployment.
- [x] Define independent scaling.
- [x] Define disposable runtime.
- [x] Define explicit agent identity.
- [x] Define capability contract.
- [x] Define bounded autonomy.
- [x] Define externalized state.
- [x] Define safe side effects.

## Architecture document

Create:

```text
docs/architecture/MICRO_AGENT_ARCHITECTURE.md
```

Include:

- [x] architectural goals
- [x] principles
- [x] Micro-Agent characteristics
- [x] cloud-native principles
- [x] distributed system implications
- [x] reference architecture
- [x] non-goals

## Acceptance

- [x] Architecture explains objectively whether a component qualifies as a Micro-Agent.

---

# Milestone 2 — Twelve-Factor Micro-Agent Model

Create:

```text
docs/architecture/TWELVE_FACTOR_MICRO_AGENT.md
```

## Map original factors

- [x] Codebase.
- [x] Dependencies.
- [x] Configuration.
- [x] Backing services.
- [x] Build/release/run.
- [x] Processes.
- [x] Port binding.
- [x] Concurrency.
- [x] Disposability.
- [x] Dev/prod parity.
- [x] Logs.
- [x] Admin processes.

## Agent-specific factors

Evaluate and define:

- [x] Agent Identity.
- [x] Capability Contract.
- [x] Bounded Autonomy.
- [x] Portable Agent Definition.
- [x] Externalized Agent State.
- [x] Agent Observability.
- [x] Safe Side Effects.
- [x] Standard Interoperability.

## Acceptance

- [x] Each factor has concrete implementation implications.
- [x] Avoid factors that are only philosophical statements.

---

# Milestone 3 — Micro-Agent Definition v1alpha1

The definition is one of the project's most important contracts.

## Metadata

- [x] API version.
- [x] Kind.
- [x] Name.
- [x] Version.
- [x] Description.
- [x] Labels.
- [x] Annotations.

## Agent behavior

- [x] Instructions.
- [x] Input contract.
- [x] Output contract.

## Dependencies

- [x] Model.
- [x] Tools.
- [x] MCP servers.
- [x] Skills.
- [x] Knowledge.
- [x] Memory.
- [x] Session.

## Runtime semantics

- [ ] Timeouts.
- [ ] Limits.
- [ ] Error policy where appropriate.
- [ ] Capability declaration.

## Interoperability

- [x] A2A configuration.
- [x] Protocol metadata.

## Security

- [x] Credential references.
- [x] Identity requirements.
- [x] Policy references.

## Schema

- [x] Define typed Python models.
- [x] Define JSON Schema.
- [x] Define YAML examples.
- [x] Reject unknown properties.
- [x] Version the schema.

## Acceptance

- [x] Definition contains no ADK-native types.
- [x] Minimal definition loads.
- [x] Invalid definitions fail with useful diagnostics.
- [ ] Definition can theoretically be consumed by another runtime.

---

# Milestone 4 — Configuration Framework

## Configuration

- [x] YAML loader.
- [ ] Environment-variable overrides.
- [x] Secret-reference model.
- [ ] Configuration precedence.
- [x] Validation.
- [x] Configuration diagnostics.

Preferred precedence:

```text
Framework Defaults
       ↓
Micro-Agent Definition
       ↓
Environment Configuration
       ↓
Secret Bindings
```

## Acceptance

- [ ] Same agent artifact can run in multiple environments without modification.

---

# Milestone 5 — Core Programming Model

## Core contracts

- [x] `MicroAgent`.
- [x] `MicroAgentDefinition`.
- [x] `AgentRequest`.
- [x] `AgentResponse`.
- [x] `AgentContext`.
- [x] `AgentCapabilities`.
- [x] `AgentIdentity`.

## Lifecycle

Contract-only (see finding A — no concrete implementation exists):

- [ ] initialize.
- [ ] start.
- [ ] ready.
- [ ] invoke.
- [ ] stop.
- [ ] shutdown.

## Acceptance

- [x] Core module has no hard dependency on ADK.

---

# Milestone 6 — Runtime SPI

Define the smallest useful runtime abstraction.

## Runtime

- [x] `AgentRuntime`.
- [x] `RuntimeAgent`.
- [x] `RuntimeCapabilities`.
- [x] Runtime lifecycle.
- [x] Invocation.
- [x] Shutdown.

Conceptual operations:

```text
create
start
invoke
stop
capabilities
```

## Rules

- [x] No framework-native types cross the public runtime boundary.
- [x] Avoid abstractions not required by the ADK implementation.
- [x] Capability reporting for optional features.

## Acceptance

- [x] Runtime API can support initial ADK vertical slice.
- [x] No hypothetical LangChain-specific abstractions are introduced.

---

# Milestone 7 — Model Support

## Model configuration

- [x] Model definition.
- [x] Provider.
- [x] Model identifier.
- [x] Endpoint.
- [x] Credential reference.
- [x] Generation configuration.
- [ ] Timeout.
- [x] Capabilities.

## Test model

- [x] Deterministic fake model.
- [x] Structured response support.
- [x] Controlled errors.
- [x] Controlled tool requests.

## Acceptance

- [x] CI requires no paid model.

---

# Milestone 8 — Tools

## Tool model

- [x] Tool definition.
- [x] Tool metadata.
- [x] Tool runtime contract.
- [x] Input schema.
- [x] Output schema.
- [ ] Timeout.
- [x] Error model.

## Example

- [x] Deterministic example tool.

## Observability

- [ ] tool invocation tracing.
- [ ] latency.
- [ ] error metrics.

---

# Milestone 9 — MCP

MCP is a first-class Micro-Agent dependency.

## Configuration

- [x] MCP definition.
- [x] Transport.
- [x] Endpoint.
- [x] Authentication reference.
- [x] Allowed capabilities.
- [x] Timeout.
- [x] Connection lifecycle.

## Runtime

- [ ] Connect to MCP server.
- [ ] Discover tools.
- [ ] Preserve resources metadata.
- [ ] Preserve prompts metadata.
- [ ] Expose allowed tools to runtime.
- [ ] Handle connection failures.
- [ ] Graceful connection shutdown.

## Security

- [ ] TLS validation.
- [ ] credential redaction.
- [ ] endpoint validation.
- [ ] response limits.

## Acceptance

- [ ] Micro-Agent can attach MCP through configuration only.

---

# Milestone 10 — Skills and Capability Contract

## Skill definition

- [x] ID.
- [x] Name.
- [x] Description.
- [x] Input metadata.
- [x] Output metadata.
- [x] Tags.

## Capability model

- [x] Expose Micro-Agent capabilities.
- [x] Distinguish Skill from Tool.
- [x] Support discovery metadata.

## Acceptance

- [x] Skills represent semantic capabilities rather than implementation functions.

---

# Milestone 11 — Session

## Session model

- [x] Session ID.
- [x] Session context.
- [x] Session metadata.
- [ ] Session lifecycle.
- [ ] Expiration.

## Providers

- [x] In-memory provider.
- [x] Persistent-provider SPI.

## Acceptance

- [ ] Multiple runtime replicas can share persistent session state when configured.

---

# Milestone 12 — Memory

## Memory model

- [x] Memory provider interface.
- [x] Memory policy.
- [x] Memory scope.
- [x] Memory entry.
- [x] Search.
- [x] Store.
- [x] Delete.
- [ ] Retention.

## Providers

- [x] In-memory test provider.
- [ ] Evaluate persistent reference implementation.

## Rules

- [x] Memory != Session.
- [x] Memory != Knowledge.
- [x] Do not persist every interaction automatically.

## Acceptance

- [ ] Runtime instance can be destroyed without losing configured persistent memory.

---

# Milestone 13 — Knowledge

## Knowledge model

- [x] Knowledge source abstraction.
- [x] Retriever interface.
- [x] External resource references.
- [ ] Versioning/hash metadata.

## Rules

- [x] Knowledge remains externally supplied information.
- [x] Avoid building an enterprise vector database.

---

# Milestone 14 — ADK Runtime Vertical Slice

## Implementation

Create:

```text
runtimes/adk/
```

Implement:

- [x] ADK runtime (scaffold only — see finding B).
- [ ] Generic ADK agent.
- [ ] Agent construction.
- [ ] Model binding.
- [ ] Native tools.
- [ ] MCP tools.
- [ ] Session integration.
- [ ] Memory integration.
- [ ] Skills metadata.
- [ ] Lifecycle.
- [ ] Graceful shutdown.

## Vertical slice

```text
micro-agent.yaml
      ↓
Definition Loader
      ↓
Micro-Agent Core
      ↓
ADK Runtime
      ↓
ADK Agent
      ↓
Fake Model
      ↓
Response
```

## Acceptance

- [x] Basic agent invocation works.
- [x] ADK types do not leak into definition/core contracts.

---

# Milestone 15 — Runtime HTTP API

## Endpoints

- [ ] `POST /v1/invoke`.
- [ ] `GET /health/live`.
- [ ] `GET /health/ready`.
- [ ] `GET /v1/capabilities`.
- [ ] Streaming if justified.

## Invocation

Support:

```text
request ID
session ID
caller metadata
input
runtime metadata
```

## Acceptance

- [ ] Micro-Agent can run as an independent network service.

---

# Milestone 16 — Health and Readiness

Define:

```text
Liveness
Readiness
Dependency Health
Capability Health
```

## Health checks

- [ ] Runtime.
- [ ] Required model.
- [ ] Required MCP.
- [ ] Session provider.
- [ ] Memory provider.

## Acceptance

- [ ] Agent can be alive but correctly report not-ready when required dependencies fail.

---

# Milestone 17 — Observability

## Logging

- [x] Structured logs.
- [x] Agent ID.
- [x] Agent version.
- [x] Invocation ID.
- [x] Session ID.
- [ ] Secret redaction.

## Metrics

- [ ] Invocation count.
- [ ] Invocation latency.
- [ ] Errors.
- [ ] Model latency.
- [ ] Tokens.
- [ ] Tool calls.
- [ ] MCP calls.
- [ ] Memory operations.

## Tracing

- [ ] OpenTelemetry.
- [ ] Agent span.
- [ ] Model spans.
- [ ] Tool spans.
- [ ] MCP spans.
- [ ] Memory spans.

## Acceptance

- [ ] One invocation can be traced through model/tool/MCP operations.

---

# Milestone 18 — Identity and Security Context

## Identity

- [x] Agent identity.
- [x] Caller identity.
- [x] User context.
- [x] Runtime/workload identity.

## Rules

- [x] Agent identity != user identity.
- [x] No implicit delegation.
- [x] No credentials inside ordinary definitions.

---

# Milestone 19 — Bounded Autonomy and Policy

## Policy

- [x] Allowed skills.
- [x] Allowed tools.
- [x] Allowed MCPs.
- [x] Model restrictions.
- [x] Side-effect policy.
- [x] Approval policy.

## Runtime enforcement

- [ ] Policies enforced outside prompt instructions where possible.

## Acceptance

- [ ] Prompt injection cannot simply override deterministic platform policy.

---

# Milestone 20 — Safe Side Effects

## Operation model

- [x] Operation ID.
- [x] Idempotency key support.
- [x] Deduplication guidance.
- [x] Retry classification.
- [x] Confirmation/approval hooks.

## Documentation

- [x] Document safe write-tool patterns.

---

# Milestone 21 — A2A

## Exposure

- [ ] Agent Card generation.
- [ ] Skills mapping.
- [ ] Endpoint.
- [ ] Security configuration.
- [ ] A2A invocation.

## Validation

- [ ] Test with compatible independent client.

## Acceptance

- [ ] Micro-Agent is interoperable without custom agent-to-agent protocol.

---

# Milestone 22 — Containerization

## Image

- [x] Production Dockerfile.
- [x] Minimal dependency footprint.
- [x] Non-root.
- [x] Arbitrary UID support where practical.
- [x] Read-only root filesystem where practical.
- [ ] External writable paths.
- [ ] Graceful SIGTERM.

## Configuration

- [x] Mounted YAML.
- [x] Environment configuration.
- [x] External secrets.

## Acceptance

- [ ] Same image runs with different Micro-Agent configuration.

---

# Milestone 23 — Kubernetes/OpenShift Baseline

## Deployment

- [x] Example Deployment.
- [x] Service.
- [x] ConfigMap.
- [x] Secret references.
- [x] Health probes.
- [x] Resources.
- [x] Multiple replicas.
- [x] Rolling update.
- [x] Pod disruption behavior.

## Acceptance

- [ ] At least two replicas operate correctly using externalized state.

---

# Milestone 24 — Architecture Validation

Build at least two independent Micro-Agent examples.

Examples:

```text
Residency Eligibility Agent
Notification Agent
```

Validate:

- [x] bounded responsibility
- [x] independent deployment
- [x] independent scaling
- [x] external state
- [x] explicit identity
- [x] explicit skills
- [ ] MCP integration
- [ ] observability
- [ ] container disposability

Use findings to revise Micro-Agent Architecture documents.

---

# Milestone 25 — Micro-Agent Definition Portability Review

Before implementing a second runtime:

- [x] Review definition for ADK-specific leakage.
- [x] Compare against current portable-agent definition efforts.
- [x] Document mandatory semantics.
- [x] Document optional semantics.
- [x] Define compatibility/versioning rules.
- [x] Define runtime capabilities.

Do not implement another runtime merely to complete this milestone.

---

# Milestone 26 — CI/CD and Release

- [x] Unit tests.
- [ ] Integration tests.
- [ ] E2E tests.
- [ ] Container tests.
- [x] Security scanning.
- [x] Dependency scanning.
- [ ] SBOM.
- [ ] Release versioning.
- [ ] Container publishing.
- [ ] Release notes.
- [ ] Documentation publishing.

---

# Micro-Agent Cloud — Future Separate Workstream

Do not implement until the standalone Micro-Agent framework is production-capable.

Initial proposed modules:

```text
micro-agent-cloud-core
micro-agent-cloud-config
micro-agent-cloud-registry
micro-agent-cloud-discovery
micro-agent-cloud-resilience
micro-agent-cloud-gateway
micro-agent-cloud-security
micro-agent-cloud-observability
micro-agent-cloud-messaging
```

---

# Micro-Agent Cloud Milestone C0 — Architecture

- [ ] Define Micro-Agent Cloud responsibilities.
- [ ] Define boundaries from Micro-Agent core.
- [ ] Document service-discovery vs agent-discovery distinction.
- [ ] Define common abstractions.
- [ ] Define extension/provider model.

---

# Micro-Agent Cloud Milestone C1 — Agent Registry

Potential contract:

```text
register
unregister
get
search
find_by_skill
find_by_capability
instances
health
```

- [ ] Define agent descriptor.
- [ ] Define semantic discovery.
- [ ] Define runtime-instance discovery.
- [ ] Define registry provider abstraction.
- [ ] Build minimal local registry.

---

# Micro-Agent Cloud Milestone C2 — Discovery

- [ ] Agent discovery client.
- [ ] Capability discovery.
- [ ] Skill discovery.
- [ ] Local caching.
- [ ] Health-aware selection.
- [ ] Integrate technical service discovery rather than replace it.

---

# Micro-Agent Cloud Milestone C3 — Distributed Configuration

- [ ] Central definition storage.
- [ ] Environment-specific overlays.
- [ ] Version management.
- [ ] Runtime retrieval.
- [ ] Configuration refresh strategy.
- [ ] Audit configuration changes.

---

# Micro-Agent Cloud Milestone C4 — Resilience

- [ ] Retry abstraction.
- [ ] Circuit breaker.
- [ ] Bulkhead.
- [ ] Rate limiting.
- [ ] Model fallback.
- [ ] Agent fallback policy where justified.
- [ ] MCP resilience.

Prefer existing mature resilience implementations.

---

# Micro-Agent Cloud Milestone C5 — Gateway

Potential:

```text
A2A routing
agent discovery
authentication
authorization
skill policy
rate limits
observability
```

- [ ] Define gateway responsibilities.
- [ ] Avoid overlapping unnecessarily with API Gateway/service mesh.
- [ ] Do not initially add semantic LLM-based routing.

---

# Micro-Agent Cloud Milestone C6 — Security and Policy

- [ ] Agent identity integration.
- [ ] Skill authorization.
- [ ] Agent-to-agent authorization.
- [ ] MCP policy.
- [ ] Delegation policy.
- [ ] Audit.

---

# Micro-Agent Cloud Milestone C7 — Distributed Observability

- [ ] Cross-agent trace propagation.
- [ ] A2A tracing.
- [ ] Registry metadata enrichment.
- [ ] Cost aggregation.
- [ ] Agent topology views.

---

# Deferred

Do not implement without demonstrated requirement:

- [ ] LangChain runtime.
- [ ] additional agent runtimes.
- [ ] visual designer.
- [ ] workflow engine.
- [ ] proprietary service mesh.
- [ ] custom container scheduler.
- [ ] autonomous infrastructure management.
- [ ] centralized multi-agent orchestrator.
- [ ] agent marketplace.
- [ ] semantic routing using another LLM.
- [ ] portable checkpoint/state migration.
- [ ] distributed memory platform.

---

# Immediate Implementation Order

Start with:

```text
1. Project Foundation
2. Micro-Agent Architecture Definition
3. Twelve-Factor Micro-Agent Model
4. Micro-Agent Definition v1alpha1
5. Configuration
6. Core Programming Model
7. Runtime SPI
8. Model support
9. Tools
10. MCP
11. Skills
12. Session
13. Memory
14. ADK runtime vertical slice
15. HTTP API
16. Health
17. Observability
18. Containerization
19. Kubernetes/OpenShift baseline
20. Architecture validation
```

Do not begin Micro-Agent Cloud implementation before the standalone Micro-Agent architecture and runtime have been validated.

---

# First End-to-End Target

The first implementation target is:

```text
micro-agent.yaml
       │
       ▼
Definition Loader
       │
       ▼
Micro-Agent Core
       │
       ▼
Runtime SPI
       │
       ▼
ADK Runtime
       │
       ├── Model
       ├── Tool
       ├── MCP
       ├── Session
       └── Memory
       │
       ▼
Micro-Agent
       │
       ▼
POST /v1/invoke
       │
       ▼
Response
```

The result should run:

```text
locally
inside a container
with external configuration
with health endpoints
with structured observability
```

before distributed Micro-Agent Cloud capabilities are implemented.