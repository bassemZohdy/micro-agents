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

`micro_agent.config.resolve_config()` implements part of this model as a
library utility. The command-line process does not currently use it, so these
values do not yet construct runtime providers.

## Environment variables recognized by `resolve_config()`

| Variable | Resolved field | Bootstrap status |
|---|---|---|
| `MICRO_AGENT_MODEL_ENDPOINT` | `model_endpoint` | not wired |
| `MICRO_AGENT_MODEL_API_KEY` | `model_api_key` | not wired |
| `MICRO_AGENT_MODEL_PROVIDER` | `model_provider` | not wired |
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

The current definition uses snake_case field names. Environment-backed
`SecretRef` resolution exists in the separate configuration model, but the
process bootstrap does not connect definition credential references to it.

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

## Development fake mode

Fake mode must be explicit before a stable release. The current CLI implicitly
uses it and therefore must not be used as evidence of a real-provider
deployment.
