---
name: micro-agents-run-and-deploy
description: Run a Micro-Agents agent as an HTTP service, call its API, bind model and state (Redis, PostgreSQL, SQLite), enable auth, audit and OpenTelemetry, and deploy it with Docker or Kubernetes. Use when starting an agent locally, wiring MICRO_AGENT_* environment variables, debugging readiness or 4xx/5xx responses, or preparing a production deployment.
license: Apache-2.0
compatibility: Needs Python 3.11+ and the micro-agents package from a source checkout or a GitHub Release wheel (not published to PyPI). Docker or kubectl are only needed for the container and cluster sections.
metadata:
  project: micro-agents
---

# Run and deploy a Micro-Agents agent

One process serves one definition. Configuration layers, lowest to highest
precedence: framework defaults, the definition, environment configuration,
`MICRO_AGENT_*` variables, resolved secrets.

## Run locally

```bash
python -m pip install -e ".[dev]"            # from a checkout; or install the wheel attached to a GitHub Release
python -m micro_agent --definition examples/support-desk-agent.yaml --host 127.0.0.1 --port 8080
```

CLI flags: `--definition` (required), `--host` (default `0.0.0.0`), `--port`
(default `8080`). The example uses `provider: fake`: deterministic, offline, and
**not** evidence of a real model deployment.

Distribution is a source checkout, the wheel/sdist on each GitHub Release, or the
signed container image (`ghcr.io/bassemzohdy/micro-agents:<tag>`, mirrored on
Docker Hub); PyPI is not a documented channel.

Optional extras: `adk`, `langgraph`, `mcp`, `redis`, `postgres`, `auth`, `otel`,
`a2a`, `aws`. Install only what the definition and deployment use; a missing
extra fails startup with an install hint rather than being ignored.

## Call it

```bash
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready          # 503 until every probed dependency is healthy
curl http://127.0.0.1:8080/v1/capabilities       # runtime capability matrix
curl -X POST http://127.0.0.1:8080/v1/invoke -H 'Content-Type: application/json' \
  -d '{"input":{"message":"hello"},"request_id":"demo-1","session_id":"s-1","timeout_seconds":15}'
```

| Endpoint | Purpose |
|---|---|
| `POST /v1/invoke` | invoke; `input` must be a JSON object; response has `output`, `status`, `error`, `metadata` |
| `GET /health/live`, `/health/ready` | liveness / readiness |
| `GET /metrics` | Prometheus text (in-memory; use OTel for multi-replica aggregation) |
| `GET /v1/openapi.json`, `/v1/docs`, `/v1/redoc` | API documents |
| `GET /.well-known/agent-card.json`, `POST /` | A2A card; JSON-RPC when `spec.interoperability.a2a.enabled` |

Error contract (stable `detail.code`): 401 `authentication_required`, 403
`authorization_denied`, 413 `request_too_large`, 422 `contract_validation_failed`,
429 `rate_limited` / `invocation_overloaded`, 503 `dependency_unavailable`, 504
`deadline_exceeded`, 500 `internal_error`. Messages are deliberately generic.

A response with `status: approval_required` carries `metadata.continuation_id`; resume
by posting it again with `approval_decision: approve` or `deny` (see skill
`micro-agents-govern-tools`).

## Bind the model

```bash
export MICRO_AGENT_MODEL_PROVIDER=openai-compatible   # fake | anthropic | azure-openai | gemini | ...
export MICRO_AGENT_MODEL_ENDPOINT=https://llm.example.com/v1
export MICRO_AGENT_MODEL_ID=example-model
export MICRO_AGENT_MODEL_API_KEY="$(from-your-secret-manager)"
```

Or put `provider`, `endpoint` and `credential_ref` in the definition. The
referenced environment variable (or Vault / AWS Secrets Manager reference) must
resolve before startup, otherwise startup fails. Never put key values in the
definition, a ConfigMap or the image.

## Bind state (shared state is required for more than one replica)

| Variable | Accepts | Notes |
|---|---|---|
| `MICRO_AGENT_SESSION_ENDPOINT` | `sqlite:///path`, `redis(s)://`, `postgres(ql)://` | needs `persistence: sqlite` or `external` in the definition |
| `MICRO_AGENT_MEMORY_ENDPOINT` | `memory://`, `redis(s)://`, `postgres(ql)://` | only when the definition declares `memory` |
| `MICRO_AGENT_IDEMPOTENCY_ENDPOINT` | `redis(s)://`, `postgres(ql)://` | shared operation de-duplication for side-effect tools |
| `MICRO_AGENT_APPROVAL_ENDPOINT` | `redis(s)://` | shared pending approvals (custom and LangGraph runtimes) |
| `MICRO_AGENT_KNOWLEDGE_ENDPOINT` | `sqlite:///path`, `https://...` | |
| `MICRO_AGENT_A2A_STORE_PATH` | SQLite path or `redis(s)://` | A2A tasks and push configs |

SQLite and in-memory stores are single-process development stores. An endpoint
with no matching declaration, or an unsupported scheme, fails fast; state is
never silently downgraded to local.

## Secure and observe

- Auth: `MICRO_AGENT_AUTH=oidc` + `MICRO_AGENT_AUTH_ISSUER` +
  `MICRO_AGENT_AUTH_AUDIENCE` (needs the `auth` extra). Health and the agent
  card stay public. A definition with `require_caller_identity` refuses to start
  without an authenticator.
- Audit: `MICRO_AGENT_AUDIT_SINK` = `stdout` (default) | `file` | `sqlite` | `none`.
- Limits: `MICRO_AGENT_MAX_REQUEST_BYTES`, `MICRO_AGENT_RATE_LIMIT_PER_MINUTE`,
  `MICRO_AGENT_CORS_ORIGINS` (off by default).
- Telemetry: `pip install -e ".[otel]"`, `MICRO_AGENT_OTEL_ENABLED=true`;
  content capture stays off unless `MICRO_AGENT_OTEL_CAPTURE_CONTENT=true`.
- Runtime: `MICRO_AGENT_RUNTIME` (skill `micro-agents-choose-runtime`).

## Container and Kubernetes

```bash
docker build -t micro-agents:dev .
docker run --rm -p 8080:8080 \
  -v "$PWD/examples/notification-agent.yaml:/etc/micro-agent/agent.yaml:ro" micro-agents:dev
```

- The image runs as non-root (UID 1001, or any UID in group 0 for OpenShift),
  exposes 8080 and probes `/health/live`. Do not add `runAsUser`.
- Reference manifests are in `deploy/kubernetes/` (`configmap`,
  `definition-configmap`, `deployment`, `service`, optional `production/`).
  Pin the image to an immutable version tag or digest, never `:latest`.
- Secrets are created out of band (`kubectl create secret generic micro-agent-secrets ...`
  or a secret manager). Never commit one.
- The same definition ships to every environment; bind environment-specific
  endpoints with `EnvironmentOverlay` or env vars.
- These are self-hosting baselines, not a managed service: the operator owns
  provider wiring, admission policy, promotion and rollback.

## Troubleshooting

| Symptom | Check |
|---|---|
| Exits at startup | The message names the cause: unresolved `credential_ref`, bare model ref, unsupported endpoint scheme, missing extra, unresolved tool or policy ref. |
| `/health/ready` 503 | A probed model, state store, knowledge source or MCP server is down. |
| 422 on invoke | `input` violates `input_contract` (missing, wrong type, or unknown field). |
| 504 | The request or definition deadline elapsed; the deadline is shared by every nested call and retries cannot extend it. |
| 429 | Rate limit, or `concurrency_policy: reject` at `max_concurrency`. |
| Sessions lost across replicas | Using memory/SQLite state; move to Redis/PostgreSQL and `persistence: external`. |

## References

- Getting started: https://bassemzohdy.github.io/micro-agents/GETTING_STARTED/
- HTTP API: https://bassemzohdy.github.io/micro-agents/API/
- Configuration: https://bassemzohdy.github.io/micro-agents/CONFIGURATION/
- Deployment: https://bassemzohdy.github.io/micro-agents/DEPLOYMENT/
