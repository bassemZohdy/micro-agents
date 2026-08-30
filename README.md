# Micro-Agents

**Micro-Agents** is an open architecture and reference framework for building cloud-native, independently deployable AI agents using principles inspired by microservices architecture and twelve-factor applications.

A **Micro-Agent** is not simply a small AI agent.

It is an independently deployable agentic component with:

- a bounded agentic capability
- explicit configuration
- explicit dependencies
- its own identity
- externalized state
- independently scalable runtime instances
- observable behavior
- explicit capabilities and skills
- standard integration interfaces
- cloud-native lifecycle semantics

The project defines:

1. **Micro-Agent Architecture**
2. **Micro-Agent Definition**
3. **Micro-Agent Framework**
4. **Runtime abstraction**
5. **Reference runtime implementations**

A related but separate project, **Micro-Agent Cloud**, may provide distributed-system capabilities such as agent registry, discovery, distributed configuration, resilience, routing, security, and observability.

---

# Vision

Microservices architecture decomposes applications into independently deployable services representing bounded business capabilities.

Micro-Agent Architecture applies similar principles to agentic systems.

```text
Monolithic Application
        ↓
Microservices

Large General-Purpose Agent
        ↓
Micro-Agents
```

Instead of building one agent containing:

```text
hundreds of tools
many unrelated responsibilities
large prompts
multiple knowledge domains
complex permissions
```

a system can consist of specialized Micro-Agents:

```text
Residency Agent
Payment Agent
Profile Agent
Notification Agent
Document Agent
Eligibility Agent
```

Each Micro-Agent owns a bounded agentic capability and can be deployed, scaled, secured, observed, upgraded, and operated independently.

---

# Micro-Agent Definition

A Micro-Agent should be definable declaratively.

Conceptually:

```yaml
apiVersion: microagents.io/v1alpha1
kind: MicroAgent

metadata:
  name: residency-renewal
  version: 1.0.0

spec:

  description: >
    Handles residency renewal activities.

  instructions: |
    Assist with residency renewal operations.
    Operate only within the declared capabilities.

  model:
    ref: reasoning-model

  skills:
    - check-eligibility
    - submit-renewal
    - check-renewal-status

  mcps:
    - ref: residency-services
    - ref: profile-services

  memory:
    ref: residency-memory

  session:
    persistence: external

  interoperability:
    a2a:
      enabled: true
```

The definition should describe the logical agent independently from a specific runtime implementation.

---

# Architecture

```text
                       MICRO-AGENTS

                    Architecture Definition
                             │
                             ▼
                   Micro-Agent Definition
                             │
                             ▼
                     Micro-Agent Core
                             │
                     Runtime Abstraction
                             │
                  ┌──────────┴──────────┐
                  │                     │
                  ▼                     ▼
            ADK Runtime            Future Runtime
               initial
```

The project should initially implement one runtime only.

Initial runtime:

```text
Google ADK
```

Additional runtimes should only be introduced when there is a demonstrated need.

---

# Micro-Agent Framework

The Micro-Agent Framework provides the programming and configuration model required to build one production-grade Micro-Agent.

Its responsibilities include:

```text
configuration
definition parsing
lifecycle
model access
MCP integration
tools
skills
memory access
session access
health
metrics
tracing
identity context
A2A integration
graceful startup/shutdown
runtime abstraction
```

It should provide sensible defaults while allowing explicit override.

The intended experience is similar in philosophy to opinionated cloud-native application frameworks:

```text
minimal configuration
        ↓
working Micro-Agent
        ↓
override only when necessary
```

---

# Runtime Abstraction

The Micro-Agent Framework must not be tied directly to one underlying agent framework.

A small runtime contract separates Micro-Agent semantics from framework-specific APIs.

Conceptually:

```python
class AgentRuntime:
    async def create(self, definition): ...

    async def start(self, agent): ...

    async def invoke(self, agent, request): ...

    async def stop(self, agent): ...

    def capabilities(self): ...
```

The initial implementation is:

```text
runtime-adk
```

The runtime abstraction should remain small.

Do not build abstraction layers for hypothetical framework differences.

---

# Micro-Agent Architecture

Micro-Agent Architecture defines architectural principles for cloud-native agent systems.

Core principles include:

```text
bounded agentic capability

independent deployment

independent scaling

explicit identity

explicit dependencies

externalized configuration

externalized persistent state

disposable runtime instances

capability-based discovery

standard communication protocols

resilience

observability

security and policy

safe side effects
```

---

# Micro-Agent vs Microservice

A Micro-Agent shares many operational properties with a Microservice but adds autonomous reasoning.

```text
Microservice
├── bounded business capability
├── API contract
├── independently deployable
├── independently scalable
└── external backing services

Micro-Agent
├── bounded agentic capability
├── capability / skill contract
├── independently deployable
├── independently scalable
├── external backing services
├── model dependency
├── MCP/tools
├── memory
├── agent identity
└── autonomous reasoning within policy
```

---

# Cloud-Native Principles

Micro-Agents should follow cloud-native and twelve-factor principles where applicable.

Examples:

```text
configuration externalized

secrets externalized

dependencies explicitly declared

persistent state stored externally

runtime processes disposable

logs emitted as streams

identical artifacts across environments

horizontal scaling through replicas

backing services treated as attached resources
```

Agent-specific extensions are additionally required.

---

# Agent-Specific Architecture Principles

Cloud-native application principles alone are insufficient for autonomous agents.

Micro-Agent Architecture therefore additionally addresses:

## Capability Contract

A Micro-Agent explicitly declares what it can do.

```yaml
skills:
  - check-eligibility
  - submit-renewal
```

The prompt itself is not the public capability contract.

---

## Bounded Autonomy

Reasoning autonomy exists only within explicit boundaries.

```text
Instructions
+
Skills
+
Tools
+
MCPs
+
Permissions
+
Policies
```

define the permitted operating space.

---

## Agent Identity

Agent identity must be distinguishable from:

```text
user identity
application identity
service identity
runtime identity
```

---

## Externalized State

Runtime instances should be disposable.

State belongs in external services.

```text
Micro-Agent Instance
       │
       ├── Session Store
       ├── Memory Store
       ├── Knowledge Store
       └── Operational Store
```

---

## Safe Side Effects

Agent actions may fail, retry, or be replayed.

Operations such as:

```text
payments
notifications
database updates
submissions
external commands
```

should support mechanisms such as:

```text
idempotency keys
operation identifiers
deduplication
approval
policy validation
```

---

# Micro-Agent Registry

Micro-Agent Architecture extends traditional service discovery.

A service registry answers:

```text
Where is payment-service?
```

An Agent Registry should additionally answer:

```text
Which agent can perform residency renewal?
```

Therefore Micro-Agent discovery contains two layers.

```text
Semantic Discovery
        ↓
Agent / Skill

Technical Discovery
        ↓
Running Endpoint / Instance
```

Technical service discovery may continue to use Kubernetes, DNS, service mesh, or other existing infrastructure.

Agent Registry adds semantic capability discovery.

---

# MCP

MCP is treated as an important Micro-Agent integration mechanism.

MCP servers act similarly to attachable backing capabilities.

```text
Micro-Agent
   │
   ├── Model
   ├── MCP
   ├── Memory
   ├── Knowledge
   └── External Services
```

MCP configuration should be externalized and reusable.

---

# A2A

A2A may be used for agent-to-agent interoperability.

```text
Micro-Agent A
      │
      │ A2A
      ▼
Micro-Agent B
```

Micro-Agent Architecture should use existing protocols where appropriate instead of defining competing protocols.

---

# Observability

Micro-Agent observability extends conventional service observability.

A trace may include:

```text
agent invocation
model invocation
token usage
memory retrieval
memory update
tool invocation
MCP invocation
A2A invocation
policy decision
side effect
latency
errors
```

OpenTelemetry should be preferred where appropriate.

---

# Project Structure

Initial repository structure:

```text
micro-agents/
│
├── docs/
│   └── architecture/
│
├── micro_agent/
│   ├── definition/
│   ├── core/
│   ├── runtime/
│   ├── config/
│   ├── lifecycle/
│   ├── models/
│   ├── tools/
│   ├── mcp/
│   ├── skills/
│   ├── memory/
│   ├── session/
│   ├── interoperability/
│   └── observability/
│
├── runtimes/
│   └── adk/
│
├── examples/
│
└── tests/
```

---

# Micro-Agent Cloud

Distributed agent-system concerns are intentionally separated from the core Micro-Agent framework.

A future or related project may provide:

```text
Micro-Agent Cloud

├── Agent Registry
├── Agent Discovery
├── Distributed Configuration
├── Agent Gateway
├── Load Balancing
├── Resilience
├── Security
├── Policy
├── Messaging
└── Distributed Observability
```

The Micro-Agent core should remain capable of running one independent Micro-Agent without requiring Micro-Agent Cloud.

---

# Technology

Initial implementation choices:

```text
Language              Python
Initial runtime       Google ADK (fake model for CI; OpenAI-compatible provider)
Configuration         YAML
HTTP runtime          FastAPI
MCP                   integration manager + security (SDK wire client pluggable)
A2A                   agent-card discovery served; full protocol client planned
Observability         telemetry facade now; OpenTelemetry exporter planned
Containers            OCI-compatible
Deployment            Kubernetes/OpenShift compatible
```

---

# Non-Goals

Initial releases do not aim to provide:

```text
workflow engine
BPMN
generic orchestration engine
visual workflow designer
custom A2A protocol
custom MCP protocol
distributed scheduler
full Micro-Agent Cloud implementation
multiple runtime implementations simultaneously
proprietary service mesh
proprietary container orchestration
```

---

# Status

The framework runs end to end: a YAML definition loads into a
`DefaultMicroAgent` bound to the ADK runtime and is served over FastAPI
(`POST /v1/invoke`, health, capability, and A2A agent-card endpoints) with a
container entrypoint and Kubernetes manifests.

Implemented today: a real agent loop with RuntimeSemantics enforcement
(timeouts, max iterations, error policy), generic tool resolution, MCP
integration with security controls (attach-by-configuration), deterministic
policy enforcement and side-effect deduplication, session/memory integration
with a persistent SQLite session provider, telemetry spans/metrics/logs with
secret redaction, and active health probes. CI covers lint, strict typing
(including `runtimes/`), schema drift, unit/integration/e2e tests, container
smoke, SBOM, and release automation.

Remaining boundaries: a production MCP wire-protocol client plugs into the
existing manager factory, OpenTelemetry export swaps into the `Telemetry`
facade, and A2A currently covers discovery (agent card), not the full task
protocol. See `CHANGELOG.md` and `TODO.md`.

See also:

```text
PROJECT_DEFINITION.md
docs/architecture/
docs/adr/
CHANGELOG.md
TODO.md
```