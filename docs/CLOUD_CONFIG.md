# Cloud Distributed Configuration (C2)

The versioned config plane, implementing the C2 backlog items on top of the
boundary in [Cloud Control-Plane Architecture](architecture/CLOUD_ARCHITECTURE.md)
and [ADR 0013](adr/0013-cloud-control-plane-boundary.md). Code lives in the
top-level `cloud` package; the core framework never imports it.

## Versioned definitions and overlays

`cloud.config.InMemoryConfigStore` keeps per-agent, append-only version
histories for two kinds of payload:

- **definitions** are validated with the core's own
  `load_definition_from_dict` — the config plane cannot store something an
  agent would fail to boot;
- **overlays** are validated with the core's `EnvironmentOverlay` model
  (deployment-only endpoint bindings; no semantics, no secrets).

Every store creates a new monotonic version with a canonical-JSON digest.
Rollbacks append the old content as a *new* version — history is never
rewritten, so the lineage stays auditable and freshly starting agents pin
the rolled-back content naturally. `get(agent, kind, version=None)` returns
a pinned or the latest version; agents pin at start and keep it across
config-plane outages (the C0 stance: the plane rolls versions, it never
mutates a running agent).

## Secret references, never secret values

Definitions carry `credential_ref` references and overlays carry endpoints —
the store keeps exactly what the core validated and nothing else. Existing
secret-management systems integrate through the one-method `SecretResolver`
protocol (`cloud.config.EnvironmentSecretResolver` reads references from
environment variables; Vault or cloud-managed stores implement the same
protocol), so secret values are resolved at use time inside the deployment
and never pass through, or live in, the config plane.

## HTTP surface

`create_config_app` (unauthenticated in C2; edge auth is C3 gateway work):

| Route | Purpose |
| --- | --- |
| `PUT /config/agents/{agent}/definition` | validate + store a new definition version (422 on invalid) |
| `PUT /config/agents/{agent}/overlay` | validate + store a new overlay version |
| `GET /config/agents/{agent}/definition[?version=n]` | latest or pinned definition |
| `GET /config/agents/{agent}/overlay[?version=n]` | latest or pinned overlay |
| `GET /config/agents/{agent}/history?kind=` | version list (digests, no payloads) |
| `POST /config/agents/{agent}/rollback` | append old content as a new version |

`cloud.config_client.ConfigClient` fetches payloads with the same
degrade-to-last-good stance as discovery: when the plane is unreachable the
last observed payload is returned annotated `from_cache: true`, and without
a cache the client raises `ConfigPlaneUnreachableError`.

## Verification

7 tests in `tests/test_cloud_config.py` cover append-only versioning,
core-side validation rejections (422), pinned/latest fetch, rollback-as-new-
version, the secret-reference contract, the HTTP surface, and outage
degradation, including authoritative client-side errors and server-error
fallback behavior. The `cloud` package remains under the strict mypy gate.
