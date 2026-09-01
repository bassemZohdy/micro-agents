# Micro-Agent Architecture

> **Normative architecture.** This document defines the intended architecture,
> not a claim that every criterion is implemented. See
> [Implementation Status](../IMPLEMENTATION_STATUS.md) for audited conformance
> and the project backlog for open work.

## 1. Architectural Goals

Micro-Agent Architecture defines an architectural style for building cloud-native, independently deployable AI agents.

Primary goals:

```text
Define what qualifies as a Micro-Agent

Provide architectural principles for agentic systems

Enable independent deployment and scaling of agent capabilities

Support standard cloud-native operational practices

Separate agent definition from runtime implementation
```

Non-goals are defined in Section 12.

---

## 2. Principles

Micro-Agent Architecture adopts principles from:

```text
Microservices Architecture
Cloud-Native Architecture
Twelve-Factor Applications
Distributed Systems
```

and extends them with agent-specific concerns.

Core principles:

```text
Bounded agentic capability
Independent deployment
Independent scaling
Explicit identity
Explicit dependencies
Externalized configuration
Externalized persistent state
Disposable runtime instances
Capability-based discovery
Standard communication protocols
Resilience
Observability
Security and policy
Safe side effects
```

---

## 3. Micro-Agent Definition

A Micro-Agent is:

> An independently deployable, narrowly scoped agentic component that owns a bounded capability, exposes explicit capabilities, externalizes configuration and persistent state, and can be independently scaled, secured, observed, upgraded, and operated.

A Micro-Agent is not defined by:

```text
number of lines of code
prompt length
number of tools
model size
```

It is defined by architectural boundaries and operational independence.

---

## 4. Bounded Agentic Capability

A Micro-Agent owns one coherent agentic responsibility.

A Micro-Agent may expose multiple related skills while maintaining one bounded domain responsibility.

Example:

```text
Residency Renewal Agent

Skills:
- check eligibility
- determine renewal requirements
- submit renewal
- check renewal status
```

A component that handles residency, payments, property, health, travel, and customer support does not qualify as a Micro-Agent.

---

## 5. Independent Deployment

A Micro-Agent should be independently deployable.

```text
Agent Definition
       +
Runtime Artifact
       ↓
Micro-Agent Deployment
       ↓
one or more replicas
```

Updating one Micro-Agent should not require rebuilding unrelated Micro-Agents.

---

## 6. Independent Scaling

Micro-Agent replicas should support independent horizontal scaling.

```text
Residency Agent

Replica 1
Replica 2
Replica 3
```

Persistent state must not depend on one runtime instance.

---

## 7. Disposable Runtime

Runtime processes should be disposable.

A Micro-Agent instance should:

```text
start quickly
handle requests immediately after readiness
shut down gracefully on SIGTERM
lose no persistent state when terminated
```

Local process memory should not be assumed to survive restart or scaling.

---

## 8. Explicit Agent Identity

Every production Micro-Agent should have an explicit identity.

Identity categories:

```text
Agent identity — the Micro-Agent itself
User identity — the caller or end user
Runtime/workload identity — the infrastructure process
Application identity — the owning system
```

Agent identity must be distinguishable from user identity.

Identity should support policy and audit requirements.

---

## 9. Capability Contract

A Micro-Agent explicitly declares what it can do.

```yaml
skills:
  - check-eligibility
  - submit-renewal
```

The prompt itself is not the public capability contract.

Capabilities should be discoverable by external systems for:

```text
routing
authorization
documentation
agent-to-agent interoperability
```

---

## 10. Bounded Autonomy

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

An LLM's theoretical capabilities do not define a Micro-Agent's permitted autonomy.

---

## 11. Externalized State

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

Separate:

```text
Session — current conversational/runtime context
Memory — information retained across interactions
Knowledge — externally supplied domain information
Operational state — runtime operational data
```

---

## 12. Safe Side Effects

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

The custom reference runtime can back these operation reservations and results
with Redis by setting `MICRO_AGENT_IDEMPOTENCY_ENDPOINT`; the local registry
remains the dependency-free default. The shared provider uses atomic claims and
TTL expiry, scopes keys by verified tenant when available, and leaves
optimistic versioning plus session/memory tenant isolation to the remaining
backlog work.

---

## 13. Cloud-Native Principles

Micro-Agents should follow cloud-native and twelve-factor principles where applicable.

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

---

## 14. Distributed System Implications

A Micro-Agent operates within a distributed system context.

Implications:

```text
network calls may fail or timeout
model providers may be unavailable
MCP servers may be unavailable
retries must be safe
state must be externalized
instances are ephemeral
multiple replicas may serve concurrent requests
```

Resilience mechanisms include:

```text
timeout
retry
circuit breaker
bulkhead
rate limiting
fallback model
```

---

## 15. Reference Architecture

```text
                       MICRO-AGENTS

                  Micro-Agent Definition
                           │
                           ▼
                   Micro-Agent Core
                           │
                   Runtime Contract
                           │
                           ▼
                    Runtime Adapter
                           │
                           ▼
                       Micro-Agent
                           │
          ┌────────────────┼────────────────┐
          │                │                │
        Model             MCP             Memory
          │                │                │
          └────────────────┼────────────────┘
                           │
                 ┌─────────┴─────────┐
                 │                   │
               HTTP                 A2A


          Cloud-Native Infrastructure

 Kubernetes / OpenShift
 Service Mesh
 Secrets
 OpenTelemetry
 External Stores
```

The runtime adapter is deliberately generic. Google ADK is the first external
framework adapter. The `runtimes/adk` package is the custom built-in
model/tool loop; the optional `runtimes/google_adk` package constructs and
exercises supported ADK APIs through the same runtime-neutral SPI.

---

## 16. Non-Goals

Do not initially build:

```text
workflow engine
BPMN
multi-agent orchestration platform
control panel
agent marketplace
full Micro-Agent Cloud
custom MCP
custom A2A
custom service mesh
custom container orchestrator
multiple runtime implementations
distributed scheduler
```

---

## 17. Qualification Criteria

A component qualifies as a Micro-Agent when it satisfies:

```text
owns one bounded agentic capability
can be deployed independently
can be scaled independently
has explicit identity
has explicit configuration
has explicit dependencies
has an explicit capability contract
externalizes persistent state
uses disposable runtime instances
produces observable behavior
enforces security boundaries
supports resilience patterns
is versioned
exposes standard interoperability interfaces
```

A component that fails to satisfy these criteria may be an AI agent but does not qualify as a Micro-Agent under this architecture.

Qualification is evidence-based. A definition or interface alone does not
establish a criterion: portability requires a second compatible consumer,
standard interoperability requires an independent standards client, and
independent scaling requires processes sharing external state under
concurrent load.
