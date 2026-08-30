# Micro-Agents — TODO

This document contains the implementation and architecture backlog for the Micro-Agents project.

Do not mark tasks complete until implementation, tests, and relevant documentation are complete.

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

- [x] Timeouts.
- [x] Limits.
- [x] Error policy where appropriate.
- [x] Capability declaration.

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
- [x] Definition can theoretically be consumed by another runtime.

---

# Milestone 4 — Configuration Framework

## Configuration

- [x] YAML loader.
- [x] Environment-variable overrides.
- [x] Secret-reference model.
- [x] Configuration precedence.
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

- [x] Same agent artifact can run in multiple environments without modification.

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

- [x] initialize.
- [x] start.
- [x] ready.
- [x] invoke.
- [x] stop.
- [x] shutdown.

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
- [x] Timeout.
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
- [x] Timeout.
- [x] Error model.

## Example

- [x] Deterministic example tool.

## Observability

- [x] tool invocation tracing.
- [x] latency.
- [x] error metrics.

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

- [x] Connect to MCP server.
- [x] Discover tools.
- [x] Preserve resources metadata.
- [x] Preserve prompts metadata.
- [x] Expose allowed tools to runtime.
- [x] Handle connection failures.
- [x] Graceful connection shutdown.

## Security

- [x] TLS validation.
- [x] credential redaction.
- [x] endpoint validation.
- [x] response limits.

## Acceptance

- [x] Micro-Agent can attach MCP through configuration only.

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
- [x] Session lifecycle.
- [x] Expiration.

## Providers

- [x] In-memory provider.
- [x] Persistent-provider SPI.

## Acceptance

- [x] Multiple runtime replicas can share persistent session state when configured.

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
- [x] Retention.

## Providers

- [x] In-memory test provider.
- [x] Evaluate persistent reference implementation.

## Rules

- [x] Memory != Session.
- [x] Memory != Knowledge.
- [x] Do not persist every interaction automatically.

## Acceptance

- [x] Runtime instance can be destroyed without losing configured persistent memory.

---

# Milestone 13 — Knowledge

## Knowledge model

- [x] Knowledge source abstraction.
- [x] Retriever interface.
- [x] External resource references.
- [x] Versioning/hash metadata.

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

- [x] ADK runtime.
- [x] Generic ADK agent.
- [x] Agent construction.
- [x] Model binding.
- [x] Native tools.
- [x] MCP tools.
- [x] Session integration.
- [x] Memory integration.
- [x] Skills metadata.
- [x] Lifecycle.
- [x] Graceful shutdown.

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

- [x] `POST /v1/invoke`.
- [x] `GET /health/live`.
- [x] `GET /health/ready`.
- [x] `GET /v1/capabilities`.
- [x] Streaming if justified.

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

- [x] Micro-Agent can run as an independent network service.

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

- [x] Runtime.
- [x] Required model.
- [x] Required MCP.
- [x] Session provider.
- [x] Memory provider.

## Acceptance

- [x] Agent can be alive but correctly report not-ready when required dependencies fail.

---

# Milestone 17 — Observability

## Logging

- [x] Structured logs.
- [x] Agent ID.
- [x] Agent version.
- [x] Invocation ID.
- [x] Session ID.
- [x] Secret redaction.

## Metrics

- [x] Invocation count.
- [x] Invocation latency.
- [x] Errors.
- [x] Model latency.
- [x] Tokens.
- [x] Tool calls.
- [x] MCP calls.
- [x] Memory operations.

## Tracing

- [x] OpenTelemetry.
- [x] Agent span.
- [x] Model spans.
- [x] Tool spans.
- [x] MCP spans.
- [x] Memory spans.

## Acceptance

- [x] One invocation can be traced through model/tool/MCP operations.

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

- [x] Policies enforced outside prompt instructions where possible.

## Acceptance

- [x] Prompt injection cannot simply override deterministic platform policy.

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

- [x] Agent Card generation.
- [x] Skills mapping.
- [x] Endpoint.
- [x] Security configuration.
- [x] A2A invocation.

## Validation

- [x] Test with compatible independent client.

## Acceptance

- [x] Micro-Agent is interoperable without custom agent-to-agent protocol.

---

# Milestone 22 — Containerization

## Image

- [x] Production Dockerfile.
- [x] Minimal dependency footprint.
- [x] Non-root.
- [x] Arbitrary UID support where practical.
- [x] Read-only root filesystem where practical.
- [x] External writable paths.
- [x] Graceful SIGTERM.

## Configuration

- [x] Mounted YAML.
- [x] Environment configuration.
- [x] External secrets.

## Acceptance

- [x] Same image runs with different Micro-Agent configuration.

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

- [x] At least two replicas operate correctly using externalized state.

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
- [x] MCP integration
- [x] observability
- [x] container disposability

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
- [x] Integration tests.
- [x] E2E tests.
- [x] Container tests.
- [x] Security scanning.
- [x] Dependency scanning.
- [x] SBOM.
- [x] Release versioning.
- [x] Container publishing.
- [x] Release notes.
- [x] Documentation publishing.

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