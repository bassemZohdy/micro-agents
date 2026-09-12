# Cloud Registry and Discovery (C1)

The minimal Micro-Agent Cloud registry and its health-aware discovery
client, implementing the boundary defined in
[Cloud Control-Plane Architecture](architecture/CLOUD_ARCHITECTURE.md) and
[ADR 0013](adr/0013-cloud-control-plane-boundary.md). The code lives in the
top-level `cloud` package: it may import the core framework, but the core
never imports it, and nothing here is needed to run a single Micro-Agent.

## Descriptors

A descriptor is the semantic half of discovery: what an agent is and who may
see it. Descriptors are **derived**, never hand-written —
`descriptor_from_definition(definition, card_url=..., card=...)` builds one
from the agent's `MicroAgentDefinition` and the payload the agent serves at
`/.well-known/agent-card.json`, and refuses the pair when the card
contradicts the definition (name, version, protocol version, or advertised
skills). The card's fingerprint is stored with the descriptor so later
drift is detectable.

Schema `v1alpha1` fields: identity (`name`, `version`, `description`),
`a2a_protocol_version`, `card_url` and `card_fingerprint`, `skills` (id,
name, description, tags, carried from the definition), `capabilities`
(booleans derived from declared dependencies: memory, tools, mcp, knowledge,
session), `labels`, and `visibility` (tenant names; empty means
unrestricted). Descriptors round-trip through plain dictionaries via
`to_dict()` / `from_dict()`; unknown schema versions are rejected, never
silently accepted.

The generated JSON Schema is
`schemas/cloud-descriptor-v1alpha1.json`; registry boundaries reject unknown
descriptor fields. See [Cloud Contract Schemas](CLOUD_SCHEMAS.md) for the
compatibility policy.

## Registry

`cloud.registry.InMemoryAgentRegistry` stores registrations with a lease: a
descriptor is healthy until its TTL lapses (default 300s) unless renewed by
heartbeat. Queries filter by `name`, `skill` id, and `tenant` visibility,
and by default include expired-lease entries marked unhealthy — the C0 rule
that stale descriptors are served with a stated age instead of vanishing;
`healthy_only=True` restricts to live leases, and entries past a retention
window are pruned on read.

`SqliteAgentRegistry` implements the same async contract with restart-safe
wall-clock leases, deterministic query ordering, stale-retention pruning, and
transactional registration/heartbeat updates. Pass `database_path=` to
`create_registry_app` to have the app construct and close the durable store,
or inject an already-owned store with the `registry=` argument. SQLite is a
portable reference backend; use a shared database implementation for multiple
registry replicas.

The FastAPI surface (`create_registry_app`, runnable standalone with
`python -m cloud.registry` on port 8090):

| Route | Purpose |
| --- | --- |
| `PUT /registry/agents/{name}/{version}` | register; payload identity must match the path (422 otherwise) |
| `POST /registry/agents/{name}/{version}/heartbeat` | renew the lease (404 when unknown) |
| `DELETE /registry/agents/{name}/{version}` | deregister |
| `GET /registry/agents?name=&skill=&tenant=&healthy_only=` | query with health rollups |

Query results are ordered deterministically by agent name and then
version (lexicographic), regardless of registration order, at both the
store and the HTTP surface — pinned by tests.

| `GET /registry/agents/{name}/{version}` | fetch one entry |
| `GET /health/ready` | registry readiness |

The registry keeps control-plane state only and is never on an agent's
serving path. Pass `authenticator=` to `create_registry_app` to protect the
API with the static or OIDC gateway authenticator; readiness remains public for
probes. Omitting it keeps the local reference app unauthenticated. The
in-memory store is the lightweight default; the SQLite store is the restart-
safe reference option.

## Discovery client

`cloud.discovery.RegistryDiscoveryClient` wraps the HTTP surface
(`register`, `heartbeat`, `deregister`, `discover`) and implements the C0
failure stance: every successful query caches its snapshot per query
shape, and when the registry is unreachable the client returns the cached
snapshot with `from_cache=True` on every hit — or raises
`RegistryUnreachableError` when no snapshot exists. Callers get a usable
answer with a stated staleness or an explicit failure, never a hang.

```python
from cloud import AgentDescriptor, RegistryDiscoveryClient

client = RegistryDiscoveryClient("http://registry:8090")
hits = await client.discover(skill="greet", tenant="acme")
for hit in hits:
    if hit.healthy or hit.from_cache:  # stale hits state themselves
        address = hit.descriptor.card_url
```

## Verification

15 tests in `tests/test_cloud_registry.py` cover derivation, card-mismatch
rejection, schema round-trips, lease expiry with a controlled clock,
tenant/skill filtering, the HTTP surface (including the 422 identity
mismatch), deterministic ordering, TTL validation, clean 404 details, and
outage degradation to a stale snapshot. The `cloud` package is part of the
strict `mypy` gate in CI.
