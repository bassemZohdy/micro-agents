# Micro-Agent Cloud — Control-Plane Architecture (C0)

C0 defines the architecture of Micro-Agent Cloud on paper only: the boundary
between the standalone framework and cloud services, the two kinds of agent
discovery, and the extension, tenancy, security, and failure models. No cloud
service is implemented in this repository, and nothing here changes the
standalone definition, runtime SPI, or single-agent serving path (see
[ADR 0013](../adr/0013-cloud-control-plane-boundary.md)).

## 1. Boundary: core framework vs cloud services

The standalone framework is the data plane. Micro-Agent Cloud is a set of
separate control-plane deployables. The boundary is ownership of concerns,
not a shared codebase:

```text
core framework (micro_agent package + runtime images)
  - parse/validate one definition, run one agent
  - model, state, tool, MCP, auth, audit, and telemetry SPIs
  - serve HTTP + A2A for that agent; enforce policy locally
  - knows nothing about other agents, tenants at scale, or the registry

cloud services (separate deployables, separate repos/images)
  - registry + discovery: versioned semantic descriptors, health rollups
  - distributed configuration: versioned definitions, overlays, secrets refs
  - gateway: A2A routing, authentication, rate limits, traffic policy
  - observability plane: trace/cost/audit aggregation and views
```

Boundary rules:

```text
the core never imports cloud code and grows no cloud-only dependencies
cloud services talk to agents as ordinary A2A/HTTP clients
an agent serves traffic with the registry, gateway, and config plane down;
  discovery and aggregation degrade, serving does not
the control plane routes and observes; it never executes a step of an agent
anything an agent needs to run must arrive through the existing
  definition + environment contract, never through an implicit cloud channel
```

The first rule is what keeps the standalone artifact honest: a Micro-Agent
image stays deployable without any cloud component, and the cloud cannot
become a hidden dependency of running one agent.

## 2. Discovery: semantic vs technical

Two different questions are often collapsed into one word, "discovery".
They have different data, different owners, and different failure modes:

```text
technical discovery — WHERE is it, and is it alive?
  answered by standard infrastructure: DNS/services, load balancers,
  instance topology, the agent's own readiness endpoint
  owned by the deployment platform; the framework already exposes
  /health/live and /health/ready for exactly this

semantic discovery — WHAT is it, and does it fit?
  answered by versioned agent descriptors in the registry:
  agent identity (name, version, owner), skills and their boundaries,
  input/output contracts, required capabilities and credentials,
  A2A protocol version, tenant visibility
  derived from the agent's own definition and A2A agent card, plus
  registration metadata; never hand-maintained facts about a live agent
```

Rules that keep them separate:

- the registry stores semantic descriptors and technical health rollups; it
  is never on the request path between a caller and an agent it already
  resolved;
- a semantic match yields an address, then ordinary technical discovery and
  the agent's readiness contract take over;
- descriptors are published from the definition and the served agent card,
  so a descriptor cannot drift from what the agent actually is — the
  registry can reject a registration whose card contradicts its descriptor;
- duplicate semantic matches are resolved by declared version and tenant
  visibility, not by load-balancing heuristics (that is technical
  discovery's job below the semantic layer).

## 3. Extension model

The core keeps exactly the extension surface it has today; cloud adds two,
both data-driven:

```text
core (unchanged): ModelProvider, SessionProvider, MemoryProvider,
  OperationRegistry, McpClient factory, Authenticator, AuditSink,
  KnowledgeRetriever, CredentialProvider, Tool registry, runtime adapter
cloud extension points:
  - descriptor schema: versioned, namespaced metadata fields on registry
    entries; unknown fields are preserved, never interpreted
  - policy hooks: gateway traffic policy expressed as declarative rules
    (auth requirements, rate limits, retries/fallback), not code
```

Cloud services must not gain a plugin API that executes inside an agent, and
the core must not grow hooks whose only caller is the cloud. When a cloud
need would tempt a core SPI change, the question is whether a standalone
deployment wants it too; if not, it belongs in the descriptor or policy
schema instead.

## 4. Tenancy model

Tenancy follows the verified-identity boundary the core already enforces:

```text
a tenant is a verified claim on the caller identity (tenant_id), not a
  configuration artifact
state providers namespace records by verified tenant; the registry,
  config plane, and observability plane namespace by the same claim
registry visibility is per tenant: descriptors declare which tenants may
  see them, and unlisted means invisible, not public-with-warning
quotas, rate limits, and audit streams are tenant-scoped at the gateway
cross-tenant reads are refused at every plane; there is no shared "global"
  tenant, only explicitly published visibility
```

## 5. Security model

```text
authentication terminates at the edge that first sees the call (gateway
  for cross-agent traffic, the agent's own auth middleware for direct
  traffic) and the verified identity propagates end to end — the existing
  InvocationIdentity chain is the only trust carrier
authorization is enforced where the resource lives: the agent's own policy
  evaluator for its tools and data, the gateway for routing and rate
  policy; the registry never grants capabilities, it only describes them
approvals stay with the agent that owns the side effect; the cloud may
  surface approval/audit events, never decide them
secrets are externalized through the existing credential-provider
  contract; the config plane distributes references, never secret values
audit is written by the agent locally (tamper-evident at the source) and
  aggregated read-only by the observability plane
```

## 6. Failure model

The standalone failure taxonomy (deadlines, bounded retries, circuit
breaking, retry suppression after side effects) stays the authority for
agent-local behavior. Cloud adds the control-plane cases:

```text
registry down        → agents serve; discovery of NEW agents degrades;
                       callers resolve from cached descriptors with a
                       stated staleness bound
config plane down    → running agents keep their pinned config version;
                       only new starts and version rolls wait
gateway down         → direct A2A to known agents still works; only
                       cross-agent routing and edge policy are lost
stale descriptor     → registration health carries a timestamp; callers
                       get the descriptor age with the address
partial failure      → per-agent circuits at the gateway mirror the
                       agent's own circuit breaker instead of a global
                       kill switch
consistency stance   → the control plane is eventually consistent metadata;
                       the data plane never requires it to serve a request
                       it has already resolved
```

The controlling principle: every cloud failure mode degrades to the
standalone system, never to a hung one.

## 7. What this definition does not decide

Deferred to C1+ implementation, deliberately: registry storage and API
shapes, descriptor file format and version negotiation, gateway
implementation, config-plane API, and the observability aggregation schema.
C0 only fixes the boundaries and models above so those designs cannot leak
control-plane concerns into the core.
