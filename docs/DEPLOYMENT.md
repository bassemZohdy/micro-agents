# Deployment Guide

The checked-in Dockerfile and manifests are development baselines, not a
production deployment.

## Container image

The image:

- installs the local Python package
- runs as non-root UID 1001 by default and supports an arbitrary runtime UID in
  group 0
- exposes port 8080
- starts `python -m micro_agent` with an externally mounted definition
- probes `/health/live`

The executable resolves an explicit fake, OpenAI-compatible, or Anthropic model
provider from the mounted definition and environment. It constructs local memory/session
providers, optional Redis/PostgreSQL-backed external memory/session/idempotency
providers, the official
MCP SDK client for declared servers, knowledge and credential providers,
policy, telemetry, and audit sinks from configuration. It can also enable the
SQLite knowledge, A2A task/push, and audit backends through their respective
environment variables.
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

The sample Deployment requests 100m CPU/128Mi memory and limits each pod to
500m CPU/512Mi memory. It spreads replicas across zones on a best-effort basis
and requires hostname spreading. The Service carries standard Prometheus
scrape annotations for `/metrics`; the dashboard panels and alert expressions
are defined in [OBSERVABILITY.md](OBSERVABILITY.md). The production overlay
adds a one-replica disruption budget, 2–10 replica CPU autoscaling, and a
default-deny ingress/egress policy with DNS and HTTPS egress.

## Supply chain

- **Image provenance**: every release tag publishes SLSA build provenance
  attestations for the container image and the Python distributions
  (`actions/attest-build-provenance`). Verify before deploying:

  ```bash
  gh attestation verify oci://ghcr.io/bassemzohdy/micro-agents@sha256:<digest> -R bassemZohdy/micro-agents
  ```

  Sign or re-attach organization policy with `cosign sign`/`cosign verify`
  if your cluster enforces signature policy.
- **Dependency locking**: the checked-in `requirements.txt` is a Linux/Python
  3.11 runtime lock generated from `pyproject.toml` with exact versions and
  distribution hashes. The Dockerfile installs it with `pip --require-hashes` before
  installing the local package without dependency resolution. Regenerate it
  only after changing runtime bounds:

  ```bash
  uv pip compile pyproject.toml \
    --python-version 3.11 \
    --python-platform x86_64-manylinux_2_28 \
    --generate-hashes --no-annotate \
    --output-file requirements.txt
  ```

  The lock targets the checked-in `python:3.11-slim` image. Development and
  optional extras intentionally remain managed from `pyproject.toml`; they are
  not part of the runtime image contract.

Before using this outside a disposable namespace:

- use an executable definition whose dependencies are actually wired
- validate network egress to model/MCP endpoints (see
  `deploy/kubernetes/production/networkpolicy.yaml`)
- add external shared state for multiple replicas
- keep the mounted definition's 25-second shutdown drain budget below the
  Deployment's 30-second `terminationGracePeriodSeconds`; the runtime cancels
  remaining invocations after the drain deadline
- enforce the same request body limit and deadline budget at the ingress or
  gateway; this is required for chunked requests and protects work before it
  reaches the application

### HTTP policy hooks

The executable keeps CORS disabled unless an explicit allowlist is supplied:

```bash
export MICRO_AGENT_CORS_ORIGINS='https://console.example,https://admin.example'
```

Use `*` only as the sole value, and do not treat it as a credentialed browser
policy. Set `MICRO_AGENT_MAX_REQUEST_BYTES` to tighten the fixed-length and
chunked request limit. Set `MICRO_AGENT_RATE_LIMIT_PER_MINUTE` to enable the
bounded process-local token bucket; use a gateway or shared datastore
implementation for replica-wide limits. The native API is versioned under
`/v1` and publishes the OpenAPI document at `/v1/openapi.json`.

## Cutting a release

The release pipeline is gated by two active GitHub rulesets: `main-required-CI`
requires every PR-visible CI check before `main` advances, and
`release-tags-immutable` makes `v*` tags undeletable and unmovable once
created — a cut release cannot be silently rewritten.

One-time setup (owner): create the pending trusted publisher on pypi.org
(Manage → Publishing) for project `micro-agents`, owner `bassemZohdy`,
repository `micro-agents`, workflow filename `release.yml`, and an **empty
environment name** (the publish job declares no GitHub environment). Without
this entry the tag-time publish job fails its OIDC exchange.

Per release:

1. Move the `[Unreleased]` CHANGELOG section into a `## [X.Y.Z] — date`
   section, set `pyproject.toml` `version`, and update the pinned image tag in
   `deploy/kubernetes/deployment.yaml` to `X.Y.Z` (the package, changelog,
   tag, schema version, and image pin must all agree —
   `python tools/validate_release.py X.Y.Z` checks this before you tag).
2. Land those edits on `main` through a pull request so the required checks
   run.
3. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`. The `Release`
   workflow then re-validates alignment, runs the full test suite and
   container smoke test, publishes the distributions to PyPI via trusted
   publishing, pushes the image to GHCR, attaches SLSA provenance and the
   SBOM, and creates the GitHub release with generated notes.

## OpenShift

The image declares UID 1001 as its ordinary Docker default, but application
paths are group-writable and the Kubernetes manifest does not pin `runAsUser`,
so an OpenShift arbitrary UID in group 0 is supported by design. Validation
under the target restricted security context constraints and read-only
filesystem remains an open production-hardening task.

## Multi-replica warning

The sample declares two replicas. SQLite remains a single-process development
reference, while Redis endpoints provide shared memory and session state across
independently scheduled pods. Install the optional Redis extra and configure
`MICRO_AGENT_MEMORY_ENDPOINT=redis://...` for declared memory plus
`MICRO_AGENT_SESSION_ENDPOINT=redis://...` (or `rediss://...`) for
`persistence: external` sessions. Set
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT=redis://...` (or `rediss://...`) for
distributed operation reservations/results in both runtimes. Operation,
session, and memory records scope keys by verified tenant when available and
use optimistic versions for updates; stale snapshots fail with a
`StateConflictError`. SQLite remains a single-process development store.
Google ADK wraps non-read-only ADK tools with the same operation registry.

## Production checklist

- [ ] real provider bootstrap, with fake mode disabled
- [ ] external definition/configuration/secret bindings
- [ ] authenticated HTTP and enabled A2A standards endpoints, with a shared
      multi-replica task/push backend where required
- [x] external session, memory, and idempotency state with tenant isolation and
      optimistic versioning
- [ ] immutable image, SBOM, signature, and provenance
- [ ] arbitrary-UID and read-only-filesystem validation
- [x] resource, disruption, autoscaling, topology, and NetworkPolicy baseline
      decisions (provider-specific selectors still require cluster review)
- [x] optional OpenTelemetry instrumentation with content capture disabled and
      bounded metric labels; configure SDK exporters before enabling in
      production
- [x] scrape `/metrics` through Service annotations and define latency, error,
      readiness, token, and cost dashboard/alert guidance
- [ ] rollback and compatibility-tested release
