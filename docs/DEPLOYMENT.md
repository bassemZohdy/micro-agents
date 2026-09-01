# Deployment Guide

The checked-in Dockerfile and manifests are development baselines, not a
production deployment.

## Container image

The image:

- installs the local Python package
- runs as UID/GID 1000
- exposes port 8080
- starts `python -m micro_agent` with an externally mounted definition
- probes `/health/live`

The executable resolves an explicit fake or OpenAI-compatible model provider
from the mounted definition and environment. It constructs local memory/session
providers, optional Redis-backed external memory/session/idempotency providers, the official
MCP SDK client for declared servers, knowledge and credential providers,
policy, telemetry, and audit sinks from configuration.
Startup probes the configured model, state providers, knowledge sources, and
declared MCP servers before readiness. Unsupported external state bindings and
unavailable credentials fail before readiness.

Keep the mounted definition identical across environments. Bind staging or
production service locations at bootstrap time with `EnvironmentOverlay` (or
provide the broader `EnvironmentConfig` when runtime/authentication settings
also vary):

```python
from micro_agent.config import EnvironmentOverlay, build_runtime

overlay = EnvironmentOverlay(
    model_endpoint="https://llm.prod.example/v1",
    mcp_endpoints={"residency-services": "https://mcp.prod.example"},
)
bootstrap = build_runtime(definition, environment=overlay)
```

The overlay is validated before runtime construction, unknown MCP refs are
rejected, and the definition is never mutated. Keep credentials in the
configured secret provider or environment rather than in either artifact.

## Kubernetes manifests

Apply order for the sample:

```bash
kubectl apply -f deploy/kubernetes/configmap.yaml
kubectl apply -f deploy/kubernetes/definition-configmap.yaml
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml
# optional production hardening:
kubectl apply -f deploy/kubernetes/production/
```

Manifest rules enforced by the test suite and CI (`kubeconform`):

- the image reference must be an immutable version tag or digest — never
  `:latest`. Resolve a digest with
  `docker buildx imagetools inspect ghcr.io/bassemzohdy/micro-agents:<tag>`
  and pin `image: ...@sha256:<digest>`.
- no runtime UID is pinned: the image runs as an arbitrary UID in group 0
  (OpenShift-compatible); keep `runAsNonRoot: true` and do not add
  `runAsUser`.
- no Secret is committed as an appliable manifest. Create it out of band:

  ```bash
  kubectl create secret generic micro-agent-secrets     --from-literal=MICRO_AGENT_MODEL_API_KEY=<value>
  ```

  or manage it with a secret manager — External Secrets Operator, Vault
  Agent Injector, or SealedSecrets — referencing the same Secret name
  (`micro-agent-secrets`). `deploy/kubernetes/secret.template.yaml` documents
  the expected keys and is excluded from `kubectl apply -f deploy/kubernetes`
  by its `.template.yaml` suffix.

## Supply chain

- **Image provenance**: every release tag publishes SLSA build provenance
  attestations for the container image and the Python distributions
  (`actions/attest-build-provenance`). Verify before deploying:

  ```bash
  gh attestation verify oci://ghcr.io/bassemzohdy/micro-agents@sha256:<digest> -R bassemZohdy/micro-agents
  ```

  Sign or re-attach organization policy with `cosign sign`/`cosign verify`
  if your cluster enforces signature policy.
- **Dependency locking**: CI installs from `pyproject.toml` bounds and runs
  `pip-audit` on every pull request; releases build from the verified
  environment. For hermetic deployments, generate a hash-pinned
  `requirements.txt` (for example with `pip-compile --generate-hashes`) from
  the committed bounds and install the image from that file; keep the lock
  file in version control and regenerate on dependency bumps only.

Before using this outside a disposable namespace:

- use an executable definition whose dependencies are actually wired
- validate network egress to model/MCP endpoints (see
  `deploy/kubernetes/production/networkpolicy.yaml`)
- add external shared state for multiple replicas
- define a shutdown deadline and cancellation policy for requests that do not
  drain in time
- enforce the same request body limit and deadline budget at the ingress or
  gateway; this is required for chunked requests and protects work before it
  reaches the application

### HTTP policy hooks

The executable keeps CORS disabled unless an explicit allowlist is supplied:

```bash
export MICRO_AGENT_CORS_ORIGINS='https://console.example,https://admin.example'
```

Use `*` only as the sole value, and do not treat it as a credentialed browser
policy. Rate limiting is an injected `RateLimiter` integration point on
`create_app()`; use a gateway or shared datastore implementation for replica-
wide limits. The native API is versioned under `/v1` and publishes the
OpenAPI document at `/v1/openapi.json`.

## OpenShift

The current fixed `USER 1000` image and `runAsUser: 1000` pod settings do not
represent OpenShift arbitrary-UID compatibility. A hardened image should use
group-writable required paths, avoid a required fixed UID, and be tested under
the restricted security context constraints used by the target cluster.

## Multi-replica warning

The sample declares two replicas. SQLite remains a single-process development
reference, while Redis endpoints provide shared memory and session state across
independently scheduled pods. Install the optional Redis extra and configure
`MICRO_AGENT_MEMORY_ENDPOINT=redis://...` for declared memory plus
`MICRO_AGENT_SESSION_ENDPOINT=redis://...` (or `rediss://...`) for
`persistence: external` sessions. Set
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT=redis://...` (or `rediss://...`) for
distributed operation reservations/results in the custom runtime. Operation,
session, and memory records scope keys by verified tenant when available and
use optimistic versions for updates; stale snapshots fail with a
`StateConflictError`. SQLite remains a single-process development store.
Google ADK rejects the binding until its mapping is implemented.

## Production checklist

- [ ] real provider bootstrap, with fake mode disabled
- [ ] external definition/configuration/secret bindings
- [ ] authenticated HTTP and enabled A2A standards endpoints
- [x] external session, memory, and idempotency state with tenant isolation and
      optimistic versioning
- [ ] immutable image, SBOM, signature, and provenance
- [ ] arbitrary-UID and read-only-filesystem validation
- [ ] resource, disruption, autoscaling, topology, and NetworkPolicy decisions
- [x] optional OpenTelemetry instrumentation with content capture disabled and
      bounded metric labels; configure SDK exporters before enabling in
      production
- [ ] scrape `/metrics` and define deployment-owned latency, error, readiness,
      token, and cost dashboards/alerts
- [ ] rollback and compatibility-tested release
