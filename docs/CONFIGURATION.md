# Configuration Reference

## Configuration layers

The intended precedence is:

```text
framework defaults
    < definition
    < environment configuration
    < environment variables
    < resolved secret binding
```

`micro_agent.config.resolve_config()` implements this precedence, and the
executable bootstrap uses it to construct the configured model provider before
the service becomes ready.

## Environment variables recognized by `resolve_config()`

| Variable | Resolved field | Bootstrap status |
|---|---|---|
| `MICRO_AGENT_MODEL_ENDPOINT` | `model_endpoint` | wired; selects OpenAI-compatible provider when set |
| `MICRO_AGENT_MODEL_ID` | `model_id` | wired; overrides the definition ref used as provider model ID |
| `MICRO_AGENT_MODEL_API_KEY` | `model_api_key` | wired; kept in provider memory only |
| `MICRO_AGENT_MODEL_PROVIDER` | `model_provider` | wired; `fake` or OpenAI-compatible aliases |
| `MICRO_AGENT_MEMORY_ENDPOINT` | `memory_endpoint` | not wired |
| `MICRO_AGENT_SESSION_ENDPOINT` | `session_endpoint` | not wired |
| `MICRO_AGENT_LOG_LEVEL` | `log_level` | not applied to Uvicorn/runtime logging |

Do not assume that setting these variables changes `python -m micro_agent`
until P0.2 is complete.

## Secret references

Definitions store references, never secret values:

```yaml
spec:
  dependencies:
    model:
      ref: reasoning-model
      credential_ref: MODEL_API_KEY
  security:
    credential_refs:
      - residency-api-key
```

The current definition uses snake_case field names. A model
`credential_ref` is resolved from that environment variable during bootstrap;
startup fails if the reference is missing. Resolved values are never included
in models, responses, logs, or exception text.

Production requirements:

- inject values from environment, Kubernetes Secret, Vault, or another
  configured secret provider
- redact values from logs, traces, errors, cards, and responses
- scope credentials to one dependency and least privilege
- fail startup when a required credential cannot be resolved

## Definition versus deployment configuration

The logical definition owns portable agent semantics. Deployment configuration
owns image, replicas, resources, namespace, runtime endpoint bindings, and
secret-provider bindings. Provider endpoints that vary by environment should
ultimately use an overlay/binding mechanism rather than editing the base
logical definition.

## Invocation limits

Definitions can bound concurrent requests at the agent boundary. The default
`wait` policy queues callers until capacity is available; `reject` returns a
runtime error immediately when the limit is full. Stopping an agent wakes
queued callers so they cannot remain blocked:

```yaml
spec:
  runtime:
    max_concurrency: 4
    concurrency_policy: wait  # or reject
```

## Development fake mode

Fake mode is explicit: set `provider: fake` in the definition or
`MICRO_AGENT_MODEL_PROVIDER=fake`. A bare model reference without a provider or
endpoint is rejected by the executable bootstrap. Fake mode is suitable for
offline development and CI only, not evidence of a real-provider deployment.
