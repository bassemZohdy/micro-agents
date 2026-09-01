# Micro-Agents

Micro-Agents defines an architectural style, a declarative definition, and a
Python reference framework for independently deployable AI agents with bounded
capabilities.

A Micro-Agent is defined by its operational boundary—not by prompt length,
model size, tool count, or lines of code. It owns one coherent agentic
capability and can be deployed, scaled, secured, observed, upgraded, and
operated independently.

> Project maturity: **pre-release reference implementation**. The definition,
> core contracts, custom loop, optional Google ADK adapter, official MCP wire
> client, A2A task server, HTTP service, and test doubles are useful today.
> The repository is not yet a production-ready platform; see
> [Implementation status](docs/IMPLEMENTATION_STATUS.md) and [TODO.md](TODO.md).

## What this repository contains

- Micro-Agent Architecture and qualification criteria
- Twelve-Factor Micro-Agent guidance
- `microagents.io/v1alpha1` Pydantic models and generated JSON Schema
- YAML definition loader and runtime-neutral core contracts
- a small `AgentRuntime` service-provider interface
- a custom reference agent loop currently located under `runtimes/adk`
- an optional Google ADK adapter under `runtimes/google_adk`
- fake and OpenAI-compatible model-provider implementations
- tool, MCP, session, memory, idempotency, knowledge, policy, health, and
  telemetry seams (including optional Redis-backed shared memory, session, and
  operation-registry providers)
- FastAPI invocation, health, capability, and standard A2A agent-card/task endpoints
- Prometheus-compatible operational metrics at `/metrics`
- container and Kubernetes/OpenShift-oriented deployment examples

The planned **Micro-Agent Cloud** workstream is separate from the standalone
framework. It may later add registry, discovery, distributed configuration,
gateway, resilience, and cross-agent observability. It is not required to run
one Micro-Agent and is gated until the standalone runtime is production-ready.

## Architecture

```text
Micro-Agent Definition
        |
        v
Framework contracts and lifecycle
        |
        v
Runtime SPI
        |
        +-- Current custom reference loop
        |
        +-- Optional Google ADK adapter
        |
        `-- Future adapters only when justified
```

The runtime-neutral contract is intentional. The custom loop remains the
lightweight default, while the optional `google-adk` extra provides a genuine
ADK adapter without leaking ADK types through the SPI. The executable bootstrap
constructs the built-in providers and selects either runtime from deployment
configuration; unsupported external providers still fail fast.

## Definition example

This is a valid minimal `v1alpha1` definition:

```yaml
apiVersion: microagents.io/v1alpha1
kind: MicroAgent
metadata:
  name: notification-agent
  version: 0.1.0
  description: Sends notifications through configured channels.
spec:
  behavior:
    instructions: |
      Send notifications through the declared capabilities only.
  dependencies:
    model:
      ref: reasoning-model
      model_id: provider-model-v2
    skills:
      - id: send-notification
        name: Send Notification
        description: Send a notification to a user.
        tags: [notification]
```

The complete schema is
[`docs/schemas/micro-agent-v1alpha1.json`](docs/schemas/micro-agent-v1alpha1.json).
The larger residency example demonstrates the available definition fields; it
also declares integrations (such as external MCP and state providers) that the
default command-line bootstrap rejects until matching providers are installed.

## Quick start

Requirements: Python 3.11 or 3.12.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m micro_agent --definition examples/notification-agent.yaml
```

Deployment-only endpoint bindings can be supplied as an `EnvironmentOverlay`
without rewriting the portable definition. Model and MCP bindings must use
absolute `http(s)` URLs; MCP keys must match declared server refs. Environment
variables still have the highest non-secret precedence, and a definition's
endpoint values remain unchanged:

```python
from micro_agent.config import EnvironmentOverlay, build_runtime

overlay = EnvironmentOverlay(
    model_endpoint="https://staging-llm.example.com/v1",
    mcp_endpoints={"residency-services": "https://staging-mcp.example.com"},
    session_endpoint="sqlite:///var/lib/micro-agent/sessions.db",
)
bootstrap = build_runtime(definition, environment=overlay)
```

The service binds to `0.0.0.0:8080` by default.

```bash
curl http://localhost:8080/health/live
curl http://localhost:8080/health/ready
curl http://localhost:8080/metrics
curl http://localhost:8080/v1/capabilities
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"message":"hello"}}'
```

The executable bootstrap now resolves the model provider from the definition
and `MICRO_AGENT_*` environment variables. The development example explicitly
sets `provider: fake`; a definition with a live endpoint (or
`MICRO_AGENT_MODEL_PROVIDER=openai-compatible`) selects
`OpenAICompatProvider`. A provider or endpoint is required—there is no silent
fallback for a bare model reference.

Runtime selection is deployment configuration. The custom loop is the default;
set `MICRO_AGENT_RUNTIME=google-adk` (with the optional `adk` extra installed)
to select the Google ADK adapter. Memory, policy, MCP, knowledge, credentials,
and telemetry mappings are validated at bootstrap; unsupported external session
bindings (anything other than the optional Redis provider) and model credential
references fail before startup rather than being silently ignored. Install
`micro-agents[redis]` and set
`MICRO_AGENT_SESSION_ENDPOINT=redis://...` for the `external` session mode, or
`MICRO_AGENT_MEMORY_ENDPOINT=redis://...` for shared memory, and
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT=redis://...` for distributed operation
deduplication. Redis writes use transactional pipelines and key TTLs so
independently scaled processes share state. Operation, session, and memory
records are scoped by the verified tenant when one is available. Provider reads
return versioned snapshots; updates advance the version and reject stale
snapshots with `StateConflictError`. The Google ADK runtime rejects the
idempotency binding until its mapping is implemented.

For an OpenAI-compatible endpoint, keep credentials out of the definition:

```bash
export MICRO_AGENT_MODEL_PROVIDER=openai-compatible
export MICRO_AGENT_MODEL_ENDPOINT=https://llm.example.com/v1
export MICRO_AGENT_MODEL_ID=example-model
export MICRO_AGENT_MODEL_API_KEY='resolve-this-outside-source-control'
python -m micro_agent --definition examples/notification-agent.yaml
```

## Container

```bash
docker build -t micro-agents:dev .
docker run --rm -p 8080:8080 \
  -v "$PWD/examples/notification-agent.yaml:/etc/micro-agent/agent.yaml:ro" \
  micro-agents:dev
```

The image smoke test verifies process startup and HTTP health endpoints with
the fake provider. It does not prove real model, MCP, authentication, external
state, or A2A task interoperability.

## Current capabilities

| Area | Current state | Production gap |
|---|---|---|
| Definition | Typed loader, generated schema, semantic uniqueness/format checks, runtime contract enforcement, deployment endpoint overlays, and a v1alpha1 compatibility fixture | versioned policy for a future API release |
| Runtime | Custom bounded model/tool loop plus deployment-selectable optional Google ADK adapter with ADK lifecycle/session/tool tests and native confirmation continuations | external production state |
| Models | Explicit fake provider and definition/environment-selected OpenAI-compatible HTTP client with tool-call transcript replay; ADK bridge accepts injected providers | broader provider credentials and remote production load testing |
| Tools | `echo` built in, schema validation, policy enforcement, and MCP adapters | documented plugin contract and safe side-effect classification |
| MCP | official SDK wire client behind the SPI, stable stdio/Streamable HTTP, legacy SSE, security checks, discovery, timeouts, reconnect, and interop tests | durable notifications and remote production load testing |
| A2A | official SDK card and JSON-RPC non-streaming task lifecycle with authenticated integration tests | streaming, push notifications, durable task store, and cancellation |
| State | definition-wired in-memory memory/session, SQLite development sessions, and optional Redis-backed external memory/sessions plus custom-runtime operation idempotency with verified-tenant namespaces, versioned snapshots, conflict detection, transactional writes, atomic claims, TTL expiry, and retention limits; startup dependency probes, bounded concurrency, cancellation-aware shutdown, and shared invocation deadlines | Google ADK idempotency mapping |
| Security | authentication, verified caller/workload propagation, policy and credential resolution, approval flow, and redacted audit events | downstream delegation and generic policy conditions |
| Observability | in-memory metrics/spans and JSON logging plus opt-in OpenTelemetry SDK traces/metrics, W3C HTTP context propagation, model/MCP outbound carriers, safe content defaults, and bounded labels | cost/token conventions and operational dashboards/alerts |
| Operations | container, package/release gates, versioned OpenAPI, request-size guard, opt-in CORS, rate-limit hook, and sample manifests | production bootstrap and OpenShift hardening |

See [Implementation status](docs/IMPLEMENTATION_STATUS.md) for evidence and
known limitations.

## Protocol baselines

The project follows released standards rather than drafts:

- A2A: v1.0.1
- MCP: specification `2025-11-25`; the July 2026 specification remains a
  release candidate until finalized

The implementation covers a tested subset of both baselines through their
official Python SDKs; streaming, durable state, and some production features
remain open. Details and official references are in [Standards](docs/STANDARDS.md).

## Development

```bash
ruff check .
ruff format --check .
mypy micro_agent runtimes
pytest -m "not integration and not e2e"
pytest -m integration
pytest -m e2e
python -m micro_agent.definition.schema
git diff --exit-code docs/schemas/
```

The current suite collects 518 tests: 433 pass in the default development
selection (plus two expected optional-dependency skips), while 83
integration/E2E/optional tests are deselected by that job. The Redis extra adds
three live integration tests in the Redis-enabled CI job, the optional Google
ADK adapter adds 15 tests, and the optional OpenTelemetry extra adds five
integration tests when those extras are installed. CI runs
lint, typing, schema, unit, integration, E2E, package, container, separate
runtime/development dependency audits, and strict documentation gates. Release
tags repeat the quality gates, validate the tag against the package version,
and publish only after all verification succeeds.

## Documentation

- [Project definition](PROJECT_DEFINITION.md)
- [Implementation status](docs/IMPLEMENTATION_STATUS.md)
- [Getting started](docs/GETTING_STARTED.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [HTTP API](docs/API.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Standards baseline](docs/STANDARDS.md)
- [Architecture](docs/architecture/MICRO_AGENT_ARCHITECTURE.md)
- [Twelve-Factor Micro-Agent](docs/architecture/TWELVE_FACTOR_MICRO_AGENT.md)
- [Architecture decisions](docs/adr/)
- [Backlog](TODO.md)
- [Changelog](CHANGELOG.md)

## Non-goals for the standalone framework

- workflow or BPMN engine
- generic multi-agent orchestration platform
- visual designer or marketplace
- custom alternatives to MCP, A2A, Kubernetes, or service mesh
- multiple runtime adapters before the first one is genuinely complete

## License

Apache License 2.0. See [LICENSE](LICENSE).
