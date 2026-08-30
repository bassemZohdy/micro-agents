# Micro-Agents — Project Definition

## Purpose

Micro-Agents defines an architectural style and a reference framework for
cloud-native, independently deployable AI agents.

The project adapts useful properties from microservices, cloud-native systems,
the Twelve-Factor App, and distributed systems to agentic workloads. It must
define concrete boundaries and operational properties; it must not merely
rename an existing agent framework.

## Definition

> A Micro-Agent is an independently deployable, narrowly scoped agentic
> component that owns a bounded capability, declares its dependencies and
> capability contract, externalizes configuration and persistent state, and
> can be independently scaled, secured, observed, upgraded, and operated.

A Micro-Agent is not defined by model size, prompt length, tool count, or
source-code size.

## Project deliverables

1. **Micro-Agent Architecture** — the architectural style and qualification
   criteria.
2. **Micro-Agent Definition** — a versioned, runtime-neutral declarative
   contract.
3. **Micro-Agent Framework** — lifecycle and integration contracts for one
   standalone agent.
4. **Runtime SPI** — the smallest practical boundary between framework
   semantics and a concrete agent runtime.
5. **Google ADK reference adapter** — the first real runtime integration.
6. **Operational baseline** — HTTP, health, security, telemetry, container,
   and Kubernetes/OpenShift behavior.

The repository currently delivers partial implementations of items 1–4, a
custom reference loop, and an optional Google ADK adapter. Operational seams
exist, but production service mappings and end-to-end security/state/protocol
integration are incomplete.

## Architectural principles

### Bounded agentic capability

Each Micro-Agent owns one coherent responsibility. A residency-renewal agent
may check eligibility, submit a renewal, and check status because those skills
belong to one bounded capability. It should not also own unrelated payment,
health, travel, and property capabilities.

### Independent deployment and scaling

One Micro-Agent can be built, released, rolled back, replicated, and scaled
without rebuilding unrelated agents.

### Runtime-neutral definition

The definition describes logical behavior, dependencies, runtime semantics,
interoperability, and security requirements without embedding framework-native
objects.

```text
Definition
    +-- metadata and version
    +-- behavior and input/output contracts
    +-- model, tools, MCP, skills, knowledge, memory, session
    +-- runtime semantics
    +-- interoperability
    `-- security references
```

Deployment concerns such as image, replicas, resource limits, autoscaling,
namespace, and secret bindings remain outside the logical definition.

### Explicit capability contract

Skills describe what the agent can do for callers. Tools describe executable
mechanisms. A skill can use several tools, and a tool does not automatically
become a public skill.

### Bounded autonomy and safe side effects

Autonomy is bounded by deterministic controls outside the prompt:

- authenticated caller and workload identity
- authorized skills, tools, and MCP servers
- policy evaluation
- confirmation or approval where required
- timeout and resource budgets
- idempotency and durable deduplication for retryable writes

Prompt instructions are not an authorization boundary.

### Externalized configuration, secrets, and state

Environment-specific values and secret material remain outside the artifact.
Persistent session, memory, knowledge, operational state, and idempotency
records use backing services. In-memory and SQLite providers are development
references, not evidence of production multi-replica state.

### Disposable and concurrent runtime instances

Runtime processes start quickly, handle concurrent invocations safely, drain
in-flight work on shutdown, and can be replaced without losing persistent
state.

### Observable operation

Logs, metrics, and traces include agent/version, request, session, caller,
model, tool, MCP, policy, side-effect, latency, usage, and error context without
leaking secrets or sensitive content.

### Standard interoperability

Use released MCP and A2A specifications instead of custom equivalents. Protocol
claims require validation with an official or independently conformant SDK,
not only project-local dataclasses.

## Logical layers

```text
Architecture
    |
    v
Micro-Agent Definition
    |
    v
Framework contracts
    |
    v
Runtime SPI
    |
    +-- Google ADK adapter (optional, current)
    `-- additional adapters only after demonstrated need
```

The framework owns shared semantics:

- definition parsing and validation
- configuration and secret references
- lifecycle and request/response contracts
- model, tool, MCP, skill, knowledge, memory, and session contracts
- identity and policy context
- health and observability
- HTTP and standards-based interoperability

Concrete runtimes own framework-specific construction and invocation behavior.
No runtime-native type crosses the common API.

## Definition compatibility

The current API version is `microagents.io/v1alpha1`.

- Unknown properties are rejected.
- Additive optional fields may be introduced within `v1alpha1`.
- Breaking semantic or structural changes require a new API version.
- A runtime must reject unsupported required capabilities rather than silently
  ignoring them.
- A definition being schema-valid does not prove that every referenced
  provider, tool, policy, or protocol capability is resolvable.

## Runtime strategy

The project keeps the custom loop as a lightweight built-in runtime and
implements Google ADK first as a separate optional adapter. Deployment
configuration can select either runtime through `MICRO_AGENT_RUNTIME`; the
`runtimes/google_adk` package is covered by ADK-native lifecycle and invocation
tests, while production service mappings remain open work.

No second third-party runtime should be added merely to demonstrate abstraction
purity.

## Protocol strategy

- A2A baseline: v1.0.1.
- MCP baseline: stable specification `2025-11-25`.
- Draft and release-candidate features are opt-in experiments and do not define
  the stable contract.
- Protocol versions are explicit in definitions, runtime capabilities, tests,
  and compatibility documentation.

See [docs/STANDARDS.md](docs/STANDARDS.md).

## Security model

The framework distinguishes:

- agent identity
- caller/client identity
- end-user and tenant context
- workload/runtime identity

Authentication occurs at the transport boundary. Authorization is evaluated
against validated identity and declared skill/tool/MCP actions. Delegation is
explicit; caller-provided metadata is never treated as authenticated identity.
Credential references are resolved through an external secret provider and
secret values never enter definitions, cards, logs, or response metadata.

The current code defines several of these data structures but does not yet
provide transport authentication, delegation, policy-reference resolution, or
an approval workflow.

## State model

Keep these concepts distinct:

| State | Purpose | Persistence expectation |
|---|---|---|
| Session | current interaction and conversation context | external when continuity is required |
| Memory | retained information across interactions | external and policy-governed |
| Knowledge | externally supplied domain information | versioned backing source |
| Operational state | task, checkpoint, idempotency, audit | durable and concurrency-safe |

SQLite can demonstrate provider behavior in one development environment. It is
not the production shared-state recommendation for independently scheduled
Kubernetes replicas.

## Operational model

The primary deployment target is Kubernetes/OpenShift-compatible OCI
containers. A production baseline requires:

- non-root and arbitrary-UID-friendly execution
- read-only root filesystem where practical
- external definitions, configuration, and secrets
- liveness distinct from readiness
- readiness failure reported with a non-success HTTP status
- graceful termination and in-flight request draining
- resource requests/limits and disruption behavior
- immutable, versioned image references
- external state and horizontally safe concurrency

## Micro-Agent Cloud boundary

Micro-Agent Cloud is a later, separate workstream for distributed concerns:

- agent registry and semantic discovery
- distributed configuration
- gateway and A2A routing
- resilience and traffic policy
- distributed authorization and audit
- cross-agent observability

It does not own the standalone definition, runtime SPI, or the ability to run
one Micro-Agent. Work starts only after the standalone production-readiness
gate in [TODO.md](TODO.md) is satisfied.

## Non-goals

- workflow or BPMN engine
- generic multi-agent orchestrator
- visual control plane or marketplace
- custom MCP or A2A protocol
- custom service mesh or container orchestrator
- distributed scheduler in the core framework
- several shallow runtime adapters instead of one complete adapter

## Success criteria

The standalone project reaches its first production-capable milestone when:

1. the definition is versioned, documented, schema-valid, and semantically
   validated;
2. a genuine Google ADK adapter constructs and runs the logical agent;
3. the executable bootstrap selects real providers and resolves external
   configuration and secrets;
4. concurrent invocations and failure recovery are safe;
5. model, tool, MCP, session, memory, knowledge, policy, and identity
   dependencies are wired or explicitly rejected;
6. MCP and A2A claims pass official-SDK compatibility tests;
7. authentication, authorization, delegation context, approval, and durable
   side-effect safety are enforced;
8. health, OpenTelemetry, container, and Kubernetes/OpenShift behavior pass
   acceptance tests;
9. all required CI and security gates are green; and
10. a versioned release can be reproduced, published, and deployed without
    silently falling back to fake behavior.

Current progress and evidence are maintained in
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md); prioritized
work is maintained only in [TODO.md](TODO.md).
