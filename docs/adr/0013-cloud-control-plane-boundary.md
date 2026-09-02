# ADR 0013: Cloud services are external control-plane deployables

## Context

Micro-Agent Cloud (registry/discovery, distributed configuration, gateway,
observability aggregation) is the next workstream. The standalone framework
already serves one agent completely: definition, runtime, state SPIs, A2A
and HTTP surface. The cloud's value is cross-agent concerns, and the risk is
that those concerns creep into the core as dependencies, hooks, or implicit
channels — making the standalone artifact worse and the cloud a hidden
requirement for running one agent.

## Decision

Micro-Agent Cloud services are separate deployables that talk to agents as
ordinary A2A/HTTP clients. The `micro_agent` package never imports cloud
code and grows no cloud-only dependencies. Discovery splits into technical
discovery (standard infrastructure plus the agent's own readiness endpoints)
and semantic discovery (versioned descriptors in a registry, derived from
the definition and the served A2A agent card). Tenancy, security, and
failure models extend the verified-identity, local-enforcement, and
degrade-to-standalone rules already in the core; the full model is recorded
in [CLOUD_ARCHITECTURE.md](../architecture/CLOUD_ARCHITECTURE.md).

## Consequences

- a Micro-Agent image remains deployable with no cloud component, and a
  resolved request never touches the control plane on the serving path;
- registry entries cannot drift from the agents they describe, because
  descriptors are checked against the served agent card at registration;
- cloud needs that would not benefit standalone deployments are expressed
  as versioned descriptor metadata or declarative gateway policy instead of
  new core SPIs;
- when the registry or config plane is down, agents keep serving (new-agent
  discovery and config rolls degrade), matching the existing stance that
  resilience failures must fail visibly and bounded, never hang;
- C1+ implementations inherit these boundaries and cannot negotiate them
  per service.
