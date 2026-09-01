# Twelve-Factor Micro-Agent

> **Normative model.** These factors describe the target operating model. They
> are not a statement of current repository conformance. See
> [Implementation Status](../IMPLEMENTATION_STATUS.md) for the audited gap
> analysis.

This document maps the original Twelve-Factor App methodology to Micro-Agent Architecture and defines agent-specific factors.

Each factor includes concrete implementation implications.

---

## Original Factors

### Factor 1 — Codebase

> One codebase tracked in version control, many deploys.

A Micro-Agent has one versioned definition and codebase.

Implementation:

```text
Micro-Agent definition is version-controlled
One definition produces one logical Micro-Agent
Different environments use configuration overlays, not separate definitions
Definition and runtime artifact are built together
```

### Factor 2 — Dependencies

> Explicitly declare and isolate dependencies.

A Micro-Agent explicitly declares all dependencies.

Implementation:

```text
model provider and identifier declared in definition
MCP server references declared in definition
tool dependencies declared in definition
skill dependencies declared in definition
Python/runtime dependencies declared in package manifest
no implicit dependency on ambient environment
```

### Factor 3 — Configuration

> Store config in the environment.

Environment-specific configuration remains outside the runtime artifact.

Implementation:

```text
model endpoints configured externally
MCP endpoints configured externally
memory store endpoints configured externally
credentials referenced, never embedded
timeouts configured externally
policies configured externally
deployment configuration separate from agent definition
```

### Factor 4 — Backing Services

> Treat backing services as attached resources.

Models, MCP servers, memory stores, and knowledge sources are attached resources.

Implementation:

```text
model provider is an attached backing service
MCP server is an attached backing service
memory store is an attached backing service
knowledge source is an attached backing service
session store is an attached backing service
backing service failure does not corrupt agent definition
backing services can be swapped through configuration
```

### Factor 5 — Build, Release, Run

> Strictly separate build and run stages.

Implementation:

```text
Build: compile/validate definition, resolve dependencies, produce artifact
Release: combine artifact with environment configuration
Run: launch runtime instance against release
Definition validation occurs at build time
Runtime instantiation occurs at run time
```

### Factor 6 — Processes

> Execute the app as one or more stateless processes.

Runtime instances are stateless.

Implementation:

```text
no persistent state in local process memory
session state stored externally
memory stored externally
any instance can serve any request
process restart loses no persistent data
```

### Factor 7 — Port Binding

> Export services via port binding.

A Micro-Agent is self-contained and exposes its own endpoints.

Implementation:

```text
HTTP API bound to configured port
health endpoints self-contained
A2A endpoint self-contained if enabled
no external web server required
```

### Factor 8 — Concurrency

> Scale out via the process model.

Implementation:

```text
horizontal scaling through replicas
each replica is independent
no shared in-memory state between replicas
replicas share external backing services
scaling decisions based on invocation load
```

### Factor 9 — Disposability

> Maximize robustness with fast startup and graceful shutdown.

Implementation:

```text
fast startup: definition loaded, runtime initialized, ready to serve
graceful shutdown on SIGTERM
finish in-flight requests before termination
release backing service connections on shutdown
no cleanup of external state required on termination
```

### Factor 10 — Dev/Prod Parity

> Keep development, staging, and production as similar as possible.

Implementation:

```text
same definition artifact across environments
same runtime artifact across environments
only configuration differs between environments
deterministic fake model available for development and testing
local MCP servers for development
```

### Factor 11 — Logs

> Treat logs as event streams.

Implementation:

```text
structured JSON logs to stdout
agent ID in every log entry
agent version in every log entry
invocation ID in every log entry
session ID where applicable
secret values redacted from log output
log aggregation handled by execution environment
```

### Factor 12 — Admin Processes

> Run admin/management tasks as one-off processes.

Implementation:

```text
definition validation as a one-off process
schema migration as a one-off process
health check as a one-off diagnostic
capability inspection as a one-off process
admin tasks do not run inside the serving process
```

---

## Agent-Specific Factors

### Factor 13 — Explicit Agent Identity

> Every Micro-Agent has an explicit, distinguishable identity.

Implementation:

```text
agent identity declared in definition
agent identity included in all observability signals
agent identity distinct from user identity
agent identity distinct from runtime/workload identity
identity supports policy evaluation and audit
```

### Factor 14 — Capability Contract

> A Micro-Agent explicitly declares its capabilities.

Implementation:

```text
skills declared in definition
skills are discoverable metadata
skills distinguish what the agent can do from how it does it
skills are not equivalent to tool function signatures
skills support routing, authorization, and documentation
```

### Factor 15 — Bounded Autonomy

> Agent autonomy operates only within explicit boundaries.

Implementation:

```text
instructions define behavioral boundaries
skills define capability boundaries
tools define executable action boundaries
MCPs define external capability boundaries
policies define permission boundaries
prompt injection cannot override deterministic platform policy
```

### Factor 16 — Portable Agent Definition

> The agent definition contains sufficient semantics for any compatible runtime to reconstruct the logical agent.

Implementation:

```text
definition uses runtime-neutral types
definition contains no ADK-native or framework-native objects
definition includes metadata, behavior, dependencies, and runtime hints
definition is versioned and schema-validated
a compatible runtime can consume the definition without modification
```

### Factor 17 — Externalized Agent State

> Agent state is stored in external backing services, not in process memory.

Implementation:

```text
session state in external session provider
long-term memory in external memory provider
knowledge in external knowledge source
operational state in external store
runtime instance destruction does not lose state
multiple replicas share state through external providers
```

### Factor 18 — Agent Observability

> Agent behavior is observable through structured signals beyond conventional logs and metrics.

Implementation:

```text
invocation tracing through model, tool, MCP, and memory operations
token usage tracking
model latency tracking
tool invocation tracking
MCP invocation tracking
memory operation tracking
policy decision tracking
cost tracking
OpenTelemetry-compatible instrumentation
```

### Factor 19 — Safe Side Effects

> Operations with side effects assume retries, failures, and possible replay.

Implementation:

```text
idempotency key support for write operations
operation identifiers for deduplication
retry classification (safe vs unsafe)
confirmation/approval hooks for sensitive operations
policy validation before side-effect execution
```

The custom runtime's optional Redis operation registry shares claims and
completed results across replicas (`MICRO_AGENT_IDEMPOTENCY_ENDPOINT`) and
expires them with a provider TTL. Operation, session, and memory keys are
scoped by verified tenant when available; snapshots carry optimistic versions
and stale non-zero-version writes are rejected. The Google ADK adapter rejects
the binding until mapped.

### Factor 20 — Standard Interoperability

> Micro-Agents communicate through standard protocols, not proprietary mechanisms.

Implementation:

```text
A2A v1.0.1 for agent-to-agent communication
MCP 2025-11-25 stable specification for tool and capability integration
HTTP API for external invocation
standard health check endpoints
no custom agent communication protocol
```

---

## Acceptance

Each factor above includes concrete implementation implications.

No factor exists as a purely philosophical statement.

Acceptance requires executable evidence at the relevant boundary. Schema
validation is not runtime portability, a fake client is not wire-protocol
interoperability, and a shared SQLite file is not an external multi-replica
state service. The optional OpenTelemetry path now exports standard
spans/metrics and bridges HTTP trace context; outbound carrier instrumentation
and production dashboards still require deployment configuration.
