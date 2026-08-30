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
is constructed programmatically with another provider.

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

For local state during development, set `MICRO_AGENT_MEMORY_ENDPOINT=memory://`
and/or `MICRO_AGENT_SESSION_ENDPOINT=memory://`. A persistent local session
store uses `MICRO_AGENT_SESSION_ENDPOINT=sqlite:///path/to/sessions.db`.
Redis, PostgreSQL, and other network state endpoints are rejected until a
production provider is implemented.

The package name `runtimes.adk` does not currently mean that Google ADK is
used. Treat this API as pre-release.

## Container

```bash
docker build -t micro-agents:dev .
docker run --rm -p 8080:8080 \
  -v "$PWD/examples/notification-agent.yaml:/etc/micro-agent/agent.yaml:ro" \
  micro-agents:dev
```

This validates the explicit fake-provider service only. It does not prove live
model, MCP, authentication, external state, or A2A task interoperability.
