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
providers, optional Redis-backed external memory/session providers, the official
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
kubectl apply -f deploy/kubernetes/secret.yaml
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml
```

Before using this outside a disposable namespace:

- replace `micro-agent:latest` with an immutable image tag or digest
- do not store real credentials in `secret.yaml`; use the platform's secret
  workflow
- use an executable definition whose dependencies are actually wired
- validate network egress to model/MCP endpoints
- add external shared state for multiple replicas
- define a shutdown deadline and cancellation policy for requests that do not
  drain in time
- enforce the same request body limit and deadline budget at the ingress or
  gateway; this is required for chunked requests and protects work before it
  reaches the application

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
`persistence: external` sessions. Idempotency state is still process-local
until its production provider is implemented.

## Production checklist

- [ ] real provider bootstrap, with fake mode disabled
- [ ] external definition/configuration/secret bindings
- [ ] authenticated HTTP and enabled A2A standards endpoints
- [ ] external session, memory, and idempotency state
- [ ] immutable image, SBOM, signature, and provenance
- [ ] arbitrary-UID and read-only-filesystem validation
- [ ] resource, disruption, autoscaling, topology, and NetworkPolicy decisions
- [ ] log/metric/trace export with sensitive-data controls
- [ ] rollback and compatibility-tested release
