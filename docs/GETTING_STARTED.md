# Getting Started

## Prerequisites

- Python 3.11 or 3.12
- Git
- Docker only for the container example

## Install for development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Validate a definition

```bash
python -c "from micro_agent.definition import load_definition_from_file; print(load_definition_from_file('examples/notification-agent.yaml').metadata)"
```

Regenerate the schema and confirm that it did not drift:

```bash
python -m micro_agent.definition.schema
git diff --exit-code docs/schemas/
```

## Run the development service

```bash
python -m micro_agent \
  --definition examples/notification-agent.yaml \
  --host 127.0.0.1 \
  --port 8080
```

The example definition explicitly selects `FakeModelProvider`. This is useful
for lifecycle and HTTP development but is not a real model configuration. The
bootstrap rejects a bare model reference so production configuration cannot
silently use the fake provider.

State bindings are explicit as well. A session with `persistence: memory` is
process-local. `persistence: sqlite` uses `MICRO_AGENT_SESSION_ENDPOINT` when
set (for example, `sqlite:///tmp/sessions.db`) and otherwise defaults to an
in-memory SQLite database for development. A declared `memory` dependency uses
the built-in in-memory provider. External state endpoints fail before startup
until a matching provider is configured. For shared memory or sessions across
processes, install `micro-agents[redis]` and bind the relevant endpoint to a
`redis://` or `rediss://` URL; use `MICRO_AGENT_MEMORY_ENDPOINT` for declared
memory, `MICRO_AGENT_SESSION_ENDPOINT` for external sessions, and
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT` for distributed operation deduplication in
the custom runtime. The Redis providers use transactional writes and key TTLs;
operation claims use an atomic `SET NX` reservation. The idempotency registry
does not yet provide tenant isolation or optimistic versioning, and Google ADK
rejects this binding. Set `persistence: external` for shared sessions.

For environment-specific model or MCP locations, keep this definition
unchanged and pass a typed `EnvironmentOverlay` to `build_runtime()`; see the
[configuration reference](CONFIGURATION.md#deployment-endpoint-overlays) for
the validation and precedence rules.

## Exercise the API

```bash
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
curl http://127.0.0.1:8080/v1/capabilities
curl -X POST http://127.0.0.1:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"message":"hello"},"request_id":"demo-1","timeout_seconds":15}'
```

Expected invoke content is the deterministic fake response unless the runtime
is constructed programmatically with another provider. The default application
also serves the standard A2A agent card at
`/.well-known/agent-card.json`; enable `spec.interoperability.a2a.enabled`
for the official SDK JSON-RPC task endpoint.

`timeout_seconds` is optional. It sets an end-to-end deadline for the request;
the runtime cancels any active model, tool/MCP, session, or memory operation
when that budget expires and the HTTP API returns 504.

## Run tests

```bash
ruff check .
ruff format --check .
mypy micro_agent runtimes
pytest -m "not integration and not e2e"
pytest -m integration
pytest -m e2e
```

The development extra includes the PyYAML typing stubs required by strict
mypy. CI runs unit-selected tests on Python 3.11 and 3.12 and runs integration
and E2E suites separately.

## Programmatic real-model injection

The executable bootstrap can select `OpenAICompatProvider` from the definition
or environment. For example:

```python
export MICRO_AGENT_MODEL_PROVIDER=openai-compatible
export MICRO_AGENT_MODEL_ENDPOINT=https://llm.example.com/v1
export MICRO_AGENT_MODEL_ID=example-model
export MICRO_AGENT_MODEL_API_KEY='set-this-from-your-secret-manager'
python -m micro_agent --definition examples/notification-agent.yaml
```

Alternatively set `provider: openai-compatible`, `endpoint`, and a
`credential_ref` in the model definition. The referenced environment variable
must exist before startup.

The package `runtimes.adk` is the lightweight custom loop. The genuine Google
ADK adapter is available separately through the optional `adk` extra:

```bash
python -m pip install -e ".[dev,adk]"
```

Construct `GoogleAdkRuntime` with an injected `ModelProvider` for deterministic
tests or with a native Google model ID for a configured Google environment:

```python
from runtimes.google_adk import GoogleAdkRuntime, GoogleAdkRuntimeConfig

runtime = GoogleAdkRuntime(GoogleAdkRuntimeConfig(model_provider=provider))
```

The adapter owns ADK agent, runner, and session objects internally; only the
runtime-neutral `AgentRuntime` contracts are exposed to callers. Memory, MCP,
policy, knowledge, credential, and telemetry mappings are validated at startup;
unsupported external session bindings and model credential references fail
fast.

## Container

```bash
docker build -t micro-agents:dev .
docker run --rm -p 8080:8080 \
  -v "$PWD/examples/notification-agent.yaml:/etc/micro-agent/agent.yaml:ro" \
  micro-agents:dev
```

This validates the explicit fake-provider service and local bootstrap only. It
does not prove a live model or external state deployment; official MCP and A2A
interoperability is covered by the integration suite when their extras are
installed.
