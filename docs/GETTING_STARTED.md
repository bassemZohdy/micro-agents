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

The command-line bootstrap currently runs `FakeModelProvider`. This is useful
for lifecycle and HTTP development but is not a real model configuration.

## Exercise the API

```bash
curl http://127.0.0.1:8080/health/live
curl http://127.0.0.1:8080/health/ready
curl http://127.0.0.1:8080/v1/capabilities
curl -X POST http://127.0.0.1:8080/v1/invoke \
  -H 'Content-Type: application/json' \
  -d '{"input":{"message":"hello"},"request_id":"demo-1"}'
```

Expected invoke content is the deterministic fake response unless the runtime
is constructed programmatically with another provider.

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

`OpenAICompatProvider` exists, but there is no supported production bootstrap
yet. Applications can inject it directly for development:

```python
from micro_agent.models import OpenAICompatConfig, OpenAICompatProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

provider = OpenAICompatProvider(
    OpenAICompatConfig(
        endpoint="https://llm.example.com/v1",
        model_id="example-model",
        api_key=None,  # resolve externally; do not hard-code credentials
        trust_env=False,  # opt in explicitly if an HTTP proxy is required
    )
)
runtime = AdkRuntime(AdkRuntimeConfig(model_provider=provider))
```

The package name `runtimes.adk` does not currently mean that Google ADK is
used. Treat this API as pre-release.

## Container

```bash
docker build -t micro-agents:dev .
docker run --rm -p 8080:8080 \
  -v "$PWD/examples/notification-agent.yaml:/etc/micro-agent/agent.yaml:ro" \
  micro-agents:dev
```

This validates the fake-provider service only.
