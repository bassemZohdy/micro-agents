# Micro-Agents

Micro-Agents defines an architectural style, a declarative definition, and a
Python reference framework for independently deployable AI agents with bounded
capabilities.

A Micro-Agent is defined by its operational boundary—not by prompt length,
model size, tool count, or lines of code. It owns one coherent agentic
capability and can be deployed, scaled, secured, observed, upgraded, and
operated independently.

> Project maturity: **pre-release reference implementation**. The definition,
> core contracts, custom agent loop, HTTP service, and test doubles are useful
> today. The repository is not yet a production-ready Google ADK runtime, MCP
> host, or A2A server. See [Implementation status](docs/IMPLEMENTATION_STATUS.md)
> and [TODO.md](TODO.md).

## What this repository contains

- Micro-Agent Architecture and qualification criteria
- Twelve-Factor Micro-Agent guidance
- `microagents.io/v1alpha1` Pydantic models and generated JSON Schema
- YAML definition loader and runtime-neutral core contracts
- a small `AgentRuntime` service-provider interface
- a custom reference agent loop currently located under `runtimes/adk`
- fake and OpenAI-compatible model-provider implementations
- tool, MCP, session, memory, knowledge, policy, health, and telemetry seams
- FastAPI invocation, health, capability, and preliminary agent-card endpoints
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
        +-- Target Google ADK adapter
        |
        `-- Future adapters only when justified
```

The runtime-neutral contract is intentional. The current implementation must
not, however, be described as Google ADK integration yet: the package has no
`google-adk` dependency and uses no ADK API. Closing that gap is the first
runtime milestone in the backlog.

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
    skills:
      - id: send-notification
        name: Send Notification
        description: Send a notification to a user.
        tags: [notification]
```

The complete schema is
[`docs/schemas/micro-agent-v1alpha1.json`](docs/schemas/micro-agent-v1alpha1.json).
The larger residency example demonstrates the available definition fields; it
also declares integrations that the default command-line bootstrap does not
yet construct.

## Quick start

Requirements: Python 3.11 or 3.12.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m micro_agent --definition examples/notification-agent.yaml
```

The service binds to `0.0.0.0:8080` by default.

```bash
curl http://localhost:8080/health/live
curl http://localhost:8080/health/ready
curl http://localhost:8080/v1/capabilities
curl -X POST http://localhost:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"message":"hello"}}'
```

Important: `python -m micro_agent` currently uses `FakeModelProvider`. It does
not yet select `OpenAICompatProvider` or other backing services from the YAML
or `MICRO_AGENT_*` environment variables. The OpenAI-compatible provider can
be injected through the Python API, but production bootstrap wiring remains
open.

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
| Definition | Typed loader and generated schema | semantic validation and compatibility policy need hardening |
| Runtime | Custom bounded model/tool loop | actual Google ADK adapter is absent |
| Models | Fake provider and injectable OpenAI-compatible HTTP client | CLI/runtime factory and credential wiring are absent |
| Tools | `echo` built in; MCP adapters can be injected | plugin registry, contract validation, and safe side-effect classification |
| MCP | interfaces, security checks, fake client, manager | official SDK wire client and protocol lifecycle |
| A2A | preliminary card generator and discovery route | A2A v1.0.1 card and task-protocol compliance |
| State | in-memory providers and SQLite session example | production shared providers and concurrency guarantees |
| Security | data types and programmatic policy evaluator | authentication, caller propagation, policy/credential resolution, approval flow |
| Observability | in-memory metrics/spans and JSON logging | OpenTelemetry export and context propagation |
| Operations | container, package/release gates, and sample manifests | production bootstrap and OpenShift hardening |

See [Implementation status](docs/IMPLEMENTATION_STATUS.md) for evidence and
known limitations.

## Protocol baselines

The project follows released standards rather than drafts:

- A2A: v1.0.1
- MCP: specification `2025-11-25`; the July 2026 specification remains a
  release candidate until finalized

The current code is not yet conformant with either baseline. Details and
official references are in [Standards](docs/STANDARDS.md).

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

The current suite contains 304 tests. CI runs lint, typing, schema, unit,
integration, E2E, package, container, separate runtime/development dependency
audits, and strict documentation gates. Release tags repeat the quality gates,
validate the tag against the package version, and publish only after all
verification succeeds.

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
