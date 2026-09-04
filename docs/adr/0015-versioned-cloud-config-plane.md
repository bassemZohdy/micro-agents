# ADR 0015: Versioned cloud config plane with use-time secret resolution

## Context

C2 calls for storing versioned definitions and environment overlays and for
integrating existing secret-management systems. The C0 boundary requires the
config plane to roll versions without ever mutating a running agent, and to
distribute references rather than secret values.

## Decision

Store definitions and overlays as append-only, per-agent version histories
validated by the core's own loader and `EnvironmentOverlay` model, with
monotonic versions and canonical-JSON digests. Rollback stores the old
content as a new version instead of rewriting history. Secret management
integrates through a one-method `SecretResolver` protocol resolved at use
time (environment variables first; Vault/cloud stores implement the same
protocol), keeping secret material out of the plane entirely. The store is
in-memory in C2, to be replaced wholesale by a durable backend later. See
[CLOUD_CONFIG.md](../CLOUD_CONFIG.md).

## Consequences

- the config plane cannot store a payload an agent would fail to boot,
  because validation is the core's own;
- running agents are immune to config-plane outages (pinned versions), and
  restarting ones can start from the client's last good payload, marked as
  such;
- rollback lineage is fully auditable and idempotent — a rollback is just
  another version;
- swapping the in-memory store for a durable one (or adding plane
  authentication with the C3 gateway) replaces a component, not a contract.
