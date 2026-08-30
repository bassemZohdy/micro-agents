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
from the mounted definition and environment. It does not yet construct real
MCP, state, policy, or non-environment secret providers.

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

## OpenShift

The current fixed `USER 1000` image and `runAsUser: 1000` pod settings do not
represent OpenShift arbitrary-UID compatibility. A hardened image should use
group-writable required paths, avoid a required fixed UID, and be tested under
the restricted security context constraints used by the target cluster.

## Multi-replica warning

The sample declares two replicas but the CLI constructs no external session or
memory provider. `SqliteSessionProvider` is a local development reference and
is not the recommended shared store for independently scheduled pods.

## Production checklist

- [ ] real provider bootstrap, with fake mode disabled
- [ ] external definition/configuration/secret bindings
- [ ] authenticated HTTP and standards endpoints
- [ ] external session, memory, and idempotency state
- [ ] immutable image, SBOM, signature, and provenance
- [ ] arbitrary-UID and read-only-filesystem validation
- [ ] resource, disruption, autoscaling, topology, and NetworkPolicy decisions
- [ ] log/metric/trace export with sensitive-data controls
- [ ] rollback and compatibility-tested release
