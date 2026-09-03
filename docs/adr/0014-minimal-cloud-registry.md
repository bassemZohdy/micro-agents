# ADR 0014: Minimal cloud registry and discovery in the `cloud` package

## Context

C1 of the cloud workstream calls for versioned agent/skill descriptors and a
minimal registry with health-aware discovery. The C0 boundary (ADR 0013)
requires that cloud services never creep into the core, while descriptors
must derive from core-owned data (the definition and the served A2A agent
card) so they cannot drift from the agents they describe.

## Decision

Implement C1 in a top-level `cloud` Python package inside this repository —
not a separate repository yet. The dependency direction is one-way and
enforced by review: `cloud` imports the core, the core never imports
`cloud`, and the `cloud` package is excluded from the published
`micro-agents` distribution. Descriptors (`v1alpha1`) are built only by
`descriptor_from_definition`, which refuses a served agent card that
contradicts the definition. The registry is an in-memory, lease-based store
behind a small FastAPI surface; the discovery client caches per-query
snapshots and serves them marked stale during registry outages.

## Consequences

- the control-plane code is reviewable and testable next to the framework
  it observes, without the core gaining a single cloud import or dependency;
- the in-memory, unauthenticated registry is explicitly minimal: persistence
  waits for the C2 config plane and edge authentication for the C3 gateway,
  and both will replace, not extend, the in-process store;
- moving `cloud` to its own repository/deployment later is a packaging step
  because the import boundary already matches the deployment boundary;
- descriptor evolution starts at `v1alpha1` with strict schema-version
  rejection, so compatibility policy (ADR 0009) applies to the registry
  contract from its first commit.
