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
| `MICRO_AGENT_RUNTIME` | `runtime` | wired; `custom` (default) or `google-adk` selects the runtime implementation |
| `MICRO_AGENT_MODEL_ENDPOINT` | `model_endpoint` | wired; selects OpenAI-compatible provider when set |
| `MICRO_AGENT_MODEL_ID` | `model_id` | wired; overrides the provider model ID without changing the logical definition ref |
| `MICRO_AGENT_MODEL_API_KEY` | `model_api_key` | wired; kept in provider memory only |
| `MICRO_AGENT_MODEL_PROVIDER` | `model_provider` | wired; `fake` or OpenAI-compatible aliases |
| `MICRO_AGENT_MEMORY_ENDPOINT` | `memory_endpoint` | wired for the built-in memory provider; external endpoints fail fast |
| `MICRO_AGENT_SESSION_ENDPOINT` | `session_endpoint` | wired for SQLite bindings; unsupported external endpoints fail fast |
| `MICRO_AGENT_LOG_LEVEL` | `log_level` | wired; applied to Uvicorn logging |

When a definition declares `memory`, the bootstrap constructs the built-in
in-memory provider. `MICRO_AGENT_MEMORY_ENDPOINT` may be `memory://` or
`inmemory://`; other endpoints are rejected until an external memory provider
is installed. Session persistence `memory` constructs an in-memory provider.
Persistence `sqlite` accepts `MICRO_AGENT_SESSION_ENDPOINT` as
`sqlite:///absolute/path` (or a plain SQLite path) and defaults to `:memory:`
for development. Persistence `external` requires an endpoint but fails fast
until a deployment supplies an external provider; it is never silently
downgraded to local state. An endpoint without a matching definition is also
rejected so configuration cannot be accidentally ignored.

## Secret references

Definitions store references, never secret values:

```yaml
spec:
  dependencies:
    model:
      ref: reasoning-model
      model_id: provider-model-v2
      credential_ref: MODEL_API_KEY
  security:
    credential_refs:
      - residency-api-key
```

A `credential_ref` is resolved through the configured credential provider
during bootstrap: by default references name environment variables, and a
deployment can inject a non-environment provider (for example
`StaticCredentialProvider` with values pre-loaded from a mounted secret or a
secret manager). Startup fails if any declared credential reference — model,
MCP server, or security — cannot be resolved. Resolved values are never
included in models, responses, logs, or exception text.

Policy references (`security.policy_refs`) resolve the same way at the
policy level: through an injected `AgentPolicy` or a configured policy
resolver callable. Unresolved policy references fail startup rather than
silently running without the declared policy.

Production requirements:

- inject values from environment, Kubernetes Secret, Vault, or another
  configured secret provider
- redact values from logs, traces, errors, cards, and responses
- scope credentials to one dependency and least privilege
- fail startup when a required credential cannot be resolved

## Runtime selection

Runtime choice belongs to deployment configuration rather than the portable
definition. The default `custom` value selects the lightweight reference loop.
Set `MICRO_AGENT_RUNTIME=google-adk` and install the optional `adk` extra to
select the Google ADK adapter:

```bash
python -m pip install -e ".[dev,adk]"
export MICRO_AGENT_RUNTIME=google-adk
```

The selector accepts `custom` (also `adk` or `reference`) and `google-adk`
(also `google_adk`). The adapter supports the native Google model path and
injected fake/OpenAI-compatible model providers. Declared services map onto
ADK-native constructs: memory bridges into an ADK memory service (auto-store
and search included), injected policy is enforced around every tool execution
and declared MCP server, declared MCP servers surface discovered tools as ADK
tools, and telemetry spans/metrics/logs wrap the runner. Both runtimes
construct knowledge and credential providers from configuration and validate
declared knowledge sources and credential references; native tools outside
the built-in registry also fail fast in both runtimes. Declarations that
still cannot be mapped under Google ADK — external (SQLite or remote)
session state and model credential references — fail fast; they are not
silently ignored.

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
    shutdown_timeout_seconds: 30
```

When `shutdown_timeout_seconds` is set, stop waits for active calls to drain
for that duration, then cancels the remaining invocation tasks before closing
the runtime. Each HTTP invocation may also set a positive `timeout_seconds` to
shorten the definition-level budget. The shortest request, definition, model,
and tool timeout is shared with session, memory, and MCP operations; a retry
cannot reset the remaining deadline.

When `behavior.input_contract` or `behavior.output_contract` declares
parameters, the runtime validates required fields, JSON-compatible types, and
unknown fields at the invocation boundary. An empty contract remains
unconstrained for compatibility with minimal development definitions.

Definitions may require runtime capabilities with
`spec.runtime.capabilities`, for example `memory` or `mcp`. Startup compares
these names with the runtime capability matrix and fails before readiness when
any required capability is unavailable. The matrix is also exposed by
`GET /v1/capabilities`. The runtime also probes the configured model, session,
memory, and declared MCP providers before marking the agent ready; a failed
probe leaves startup in an error state instead of deferring the failure to the
first invocation.

## Development fake mode

Fake mode is explicit: set `provider: fake` in the definition or
`MICRO_AGENT_MODEL_PROVIDER=fake`. A bare model reference without a provider or
endpoint is rejected by the executable bootstrap. Fake mode is suitable for
offline development and CI only, not evidence of a real-provider deployment.
