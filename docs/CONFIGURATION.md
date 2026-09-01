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

## Environment variables recognized by `resolve_config()` and telemetry bootstrap

| Variable | Resolved field | Bootstrap status |
|---|---|---|
| `MICRO_AGENT_RUNTIME` | `runtime` | wired; `custom` (default) or `google-adk` selects the runtime implementation |
| `MICRO_AGENT_MODEL_ENDPOINT` | `model_endpoint` | wired; selects OpenAI-compatible provider when set |
| `MICRO_AGENT_MODEL_ID` | `model_id` | wired; overrides the provider model ID without changing the logical definition ref |
| `MICRO_AGENT_MODEL_API_KEY` | `model_api_key` | wired; kept in provider memory only |
| `MICRO_AGENT_MODEL_PROVIDER` | `model_provider` | wired; `fake` or OpenAI-compatible aliases |
| `MICRO_AGENT_MEMORY_ENDPOINT` | `memory_endpoint` | wired for built-in memory or Redis (`redis://`/`rediss://`) memory; unsupported endpoints fail fast |
| `MICRO_AGENT_SESSION_ENDPOINT` | `session_endpoint` | wired for SQLite or Redis (`redis://`/`rediss://`) bindings; unsupported external endpoints fail fast |
| `MICRO_AGENT_IDEMPOTENCY_ENDPOINT` | `idempotency_endpoint` | wired for the custom runtime's Redis-backed distributed operation registry; unsupported endpoints fail fast |
| `MICRO_AGENT_LOG_LEVEL` | `log_level` | wired; applied to Uvicorn logging |
| `MICRO_AGENT_CORS_ORIGINS` | `cors_origins` | wired; comma-separated absolute HTTP(S) origins, or `*` alone |
| `MICRO_AGENT_OTEL_ENABLED` | telemetry bootstrap | wired; opt-in OpenTelemetry instrumentation (`true`/`false`, default `false`) |
| `MICRO_AGENT_OTEL_SERVICE_NAME` | telemetry bootstrap | wired; service name for standard traces/metrics (falls back to `OTEL_SERVICE_NAME`) |
| `MICRO_AGENT_OTEL_CAPTURE_CONTENT` | telemetry bootstrap | wired; opt-in bounded content attributes (default `false`) |
| `MICRO_AGENT_OTEL_MAX_ATTRIBUTE_LENGTH` | telemetry bootstrap | wired; positive bound for attribute/label strings (default `256`) |
| `MICRO_AGENT_OTEL_MAX_LABEL_VALUES` | telemetry bootstrap | wired; maximum distinct values per metric label (default `100`, overflow is `[OTHER]`) |
| `MICRO_AGENT_OTEL_INPUT_COST_PER_1K_USD` | telemetry bootstrap | optional non-negative input-token price used for `model_cost_usd_total` |
| `MICRO_AGENT_OTEL_OUTPUT_COST_PER_1K_USD` | telemetry bootstrap | optional non-negative output-token price used for `model_cost_usd_total` |

When a definition declares `memory`, the bootstrap constructs the built-in
in-memory provider when `MICRO_AGENT_MEMORY_ENDPOINT` is `memory://` or
`inmemory://` (or unset). A `redis://` or `rediss://` endpoint selects the
optional Redis memory provider, which stores scoped JSON records in a shared
namespace, enforces `MemoryPolicy` TTL/capacity limits, and cleans expired
records. Other endpoints fail fast until a matching provider is installed.
Session persistence `memory` constructs an in-memory provider.
Persistence `sqlite` accepts `MICRO_AGENT_SESSION_ENDPOINT` as
`sqlite:///absolute/path` (or a plain SQLite path) and defaults to `:memory:`
for development. Persistence `external` accepts `redis://` or `rediss://`
endpoints when the optional `redis` extra is installed
(`pip install 'micro-agents[redis]'`). The Redis provider uses transactional
writes, Redis key TTLs, and a shared index so independently scaled processes
can share session state. Other endpoints fail fast; the declaration is never
silently downgraded to local state. An endpoint without a matching definition
is also rejected so configuration cannot be accidentally ignored.

The custom runtime can share operation reservations and completed results across
replicas with `MICRO_AGENT_IDEMPOTENCY_ENDPOINT`. Redis (`redis://` or
`rediss://`) is supported when the optional client is installed. Reservations
use an atomic `SET ... NX` claim and completed results expire after the provider
TTL (one day by default). When a verified tenant identity is present, operation
keys are namespaced by that tenant; unverified/local calls retain the legacy
provider-wide namespace. Session and memory records use the same verified
tenant boundary. Reads return a versioned snapshot (starting at version 1),
and writing a stale snapshot raises `StateConflictError`; zero-version writes
retain the legacy unconditional behavior. The Google ADK runtime rejects this
binding until its distributed idempotency mapping is implemented.

HTTP CORS is disabled unless `MICRO_AGENT_CORS_ORIGINS` is set. The executable
passes this allowlist to `create_app`; application embedders can use the same
policy with `create_app(cors_origins=[...])`. The default policy never enables
credentials. Rate limiting is intentionally an injected `RateLimiter` hook,
not an environment-selected local counter; deployments should provide a
shared gateway or datastore implementation to `create_app(rate_limiter=...)`.

```bash
pip install 'micro-agents[redis]'
export MICRO_AGENT_SESSION_ENDPOINT='rediss://sessions.example:6380/0'
export MICRO_AGENT_IDEMPOTENCY_ENDPOINT='rediss://operations.example:6380/0'
```

## Deployment endpoint overlays

Keep the definition portable and bind environment-specific service locations
at bootstrap time with `EnvironmentOverlay`:

```python
from micro_agent.config import EnvironmentOverlay, build_runtime

overlay = EnvironmentOverlay(
    model_endpoint="https://staging-llm.example.com/v1",
    mcp_endpoints={"rules": "https://staging-mcp.example.com"},
    memory_endpoint="memory://",
    session_endpoint="sqlite:///var/lib/micro-agent/sessions.db",
    idempotency_endpoint="rediss://operations.example:6380/0",
)
bootstrap = build_runtime(definition, environment=overlay)
```

The overlay is a typed deployment layer. `model_endpoint` and every
`mcp_endpoints` value must be an absolute `http` or `https` URL; MCP keys must
match a server `ref` declared by the definition. Overriding a stdio MCP
server is rejected because stdio is selected by its command and arguments,
not a network endpoint. Memory and session endpoints retain their provider
specific schemes. An overlay is copied into runtime configuration and never
mutates the `MicroAgentDefinition` object.

The effective precedence is framework defaults, definition, an explicit
`EnvironmentConfig` or `EnvironmentOverlay`, `MICRO_AGENT_*` environment
variables, and finally resolved secret bindings. Unknown MCP bindings fail
before runtime creation instead of being silently ignored. Pass an
`EnvironmentConfig` directly when non-endpoint deployment fields (for
example, runtime or authentication) also need to be supplied.

## API compatibility fixtures and migration

`tests/fixtures/compatibility/v1alpha1-minimal.yaml` is the canonical small
fixture for the supported `microagents.io/v1alpha1` shape. The loader and
generated schema reject other API versions and unknown fields, so a future
version must be introduced as a new versioned model and fixture rather than
silently widening v1alpha1. During migration, validate the new fixture in CI,
keep endpoint bindings in an overlay, and update the schema, compatibility
tests, this guide, and the changelog together. Existing v1alpha1 definitions
remain valid until an explicit migration policy for the new API version is
published.

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

## Transport authentication

Inbound authentication is selected through external configuration, behind an
`Authenticator` SPI. OIDC/OAuth2 Bearer JWT validation is implemented first
as the dominant scheme; the default `none` mode leaves `/v1/invoke`
unauthenticated for development.

| Variable | Meaning |
|---|---|
| `MICRO_AGENT_AUTH` | `none` (default) or `oidc` |
| `MICRO_AGENT_AUTH_ISSUER` | OIDC issuer URL (required for `oidc`); JWKS is discovered from the issuer |
| `MICRO_AGENT_AUTH_AUDIENCE` | Expected `aud` claim (required for `oidc`) |

Behavior when `oidc` is configured:

- `/v1/invoke` requires `Authorization: Bearer <JWT>`; tokens are verified
  for asymmetric signature (JWKS), issuer, audience, and expiry, and must
  carry `sub`. Standard claims map to verified caller and user/tenant
  identity (`sub`, `preferred_username`, `tid`/`tenant_id`, `roles`,
  `client_id` for service callers).
- Unauthenticated calls fail with the stable 401 `authentication_required`
  contract before the agent is reached.
- Health probes and the A2A discovery card remain public by design.
- A definition declaring `security.identity_requirements.require_caller_identity`
  fails app creation if no authenticator is configured.

Verification requires the optional `auth` extra (`PyJWT`): install with
`pip install 'micro-agents[auth]'`.

## Audit events

Security-relevant decisions — policy denials (tool, side effect, skill,
model, MCP), approval decisions, and authentication failures — are recorded
through a configurable audit sink. Sensitive keys are redacted at write
time; events carry identifiers and reasons, never payloads or credentials.

| Variable | Meaning |
|---|---|
| `MICRO_AGENT_AUDIT_SINK` | `stdout` (default), `file`, or `none` |
| `MICRO_AGENT_AUDIT_FILE` | Append path; required when the sink is `file` |

The default `stdout` sink writes one JSON object per line for platform log
collection (the 12-factor durability path); `file` appends to a local file
for deployments without a log pipeline.

## OpenTelemetry

Install the optional instrumentation extra and enable it explicitly:

```bash
pip install 'micro-agents[otel]'
export MICRO_AGENT_OTEL_ENABLED=true
export MICRO_AGENT_OTEL_SERVICE_NAME=orders-agent
```

`Telemetry` keeps the in-memory metrics and span tree for deterministic tests
and adds SDK-backed spans, counters, histograms, and W3C `traceparent`/
`tracestate` extraction and injection when enabled. Model requests and
SDK-backed MCP HTTP requests inject the active carrier on each outbound call;
tool and A2A work stays in the same in-process span context. Configure the
OpenTelemetry SDK/exporter using the standard `OTEL_*` environment variables or
an embedding process's providers. Content-bearing attributes are suppressed by
default; only enable `MICRO_AGENT_OTEL_CAPTURE_CONTENT` after reviewing
redaction, retention, and cost controls. Label values are truncated and capped
to avoid unbounded cardinality. Dashboard/alert definitions remain deployment
follow-up work.

Token metrics use `model_tokens_total` with a `token_type` label (`prompt`,
`completion`, and `total`); `total` is taken from the provider when available
and otherwise equals prompt plus completion tokens. Optional USD prices per
1,000 input/output tokens produce `model_cost_usd_total`; no cost is inferred
when prices are not configured. The `/metrics` endpoint exposes in-memory
operational series in Prometheus text format for a scraper.

## Approval continuation

When a policy sets `approval_required`, side-effect tool calls pause the
invocation instead of silently executing or permanently failing: the
response returns `status: approval_required` with a `continuation_id` and
the pending tool names. The caller resumes the same invocation by posting
`continuation_id` with `approval_decision: approve` (the pending tools
execute; hard policy denials still apply) or `deny` (the model receives the
denial and can respond). Continuations expire after five minutes of
inactivity; unknown or expired ids fail with a stable 404.

The Google ADK adapter maps the same request onto ADK's native experimental
`ToolConfirmation` flow. ADK continuations are backed by the original ADK
session, so resume requests must include the original `session_id`; the
returned metadata also includes the pending tool names, approval hints, and
payloads. The adapter keeps ADK classes behind the runtime SPI and does not
enable the feature for runtimes that do not advertise the adapter.

## Tool side-effect classification

Declare the retry and approval semantics of each tool explicitly. The
classification is part of the portable definition, so both runtime adapters
apply the same policy:

```yaml
spec:
  dependencies:
    tools:
      - name: profile
        source: native
        side_effect: read_only
      - name: charge-card
        source: mcp
        side_effect: idempotent
      - name: send-notification
        source: native
        side_effect: unsafe
```

`read_only` means the tool must not write external state. It is still subject
to the ordinary tool allow/deny policy, but it does not trigger side-effect
approval or an operation-registry claim. `idempotent` means a stable
`idempotency_key` can safely deduplicate a retry; `unsafe` may have a
non-repeatable effect and remains approval/policy gated. Both `idempotent` and
`unsafe` use the operation registry when one is configured.

The field defaults to `unsafe` for backward compatibility and fail-closed
behavior. Runtime metadata for injected or discovered tools also defaults to
`unsafe` when no declaration overrides it. A classification documents the
contract; it does not make an implementation idempotent or add automatic
retry logic. The custom runtime suppresses its whole-invocation retry policy
after any non-read-only tool has started, including when a later model call or
operation-record write fails. This conservative rule avoids replaying a write
whose final outcome is unknown; retry budgets, backoff, and reconciliation
remain deployment work.

## MCP servers

Declared MCP servers connect through the official MCP SDK (stable
`2025-11-25`) when the optional `mcp` extra is installed
(`pip install 'micro-agents[mcp]'`); without it, startup fails with an
installation message rather than ignoring the declarations. Streamable HTTP
and stdio are the standard transports; `sse` exists for legacy migration
only.

```yaml
spec:
  dependencies:
    mcp_servers:
      - ref: profile-services
        transport: streamable-http
        endpoint: https://mcp.internal.example.com
        credential_ref: MCP_TOKEN
        timeout_seconds: 15
      - ref: local-tools
        transport: stdio
        command: python
        args: ["-m", "tools.mcp_server"]
```

stdio servers are modeled by a local `command` and `args` (never an
endpoint); HTTP-based servers require an https endpoint unless they are on
loopback. Declared `credential_ref` values resolve through the configured
credential provider at connect time — an Authorization header for HTTP
servers, and an environment variable named after the reference for stdio
child processes — and are never stored on config objects or logs.

The SDK client automatically reconnects after an unexpected transport drop.
Each new connection renegotiates the MCP protocol and capabilities. The retry
budget is bounded (three attempts by default) with exponential backoff; an
explicit runtime shutdown suppresses reconnects, and an exhausted budget keeps
the dependency unhealthy so readiness does not report a recovered server.

## Workload identity

The runtime resolves its own workload identity once per process for audit
attribution and invocation context:

| Variable | Meaning |
|---|---|
| `MICRO_AGENT_WORKLOAD_ID` | Explicit workload identifier (overrides discovery) |
| `MICRO_AGENT_WORKLOAD_NAMESPACE` | Explicit namespace (overrides discovery) |
| `MICRO_AGENT_SERVICE_ACCOUNT` | Service-account name |

Without overrides, the Kubernetes service-account namespace mount
(`/var/run/secrets/kubernetes.io/serviceaccount/namespace`) supplies the
namespace and the pod hostname the workload id; outside Kubernetes the
hostname is used with the `default` namespace.

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

OpenAI-compatible model endpoints preserve any path prefix in the configured
URL. For example, `https://llm.example.com/v1` is probed at
`/v1/models` and invoked at `/v1/chat/completions`; the provider also sends the
resolved `model_id` while the logical `ref` remains unchanged.

## Definition versus deployment configuration

The logical definition owns portable agent semantics. Deployment configuration
owns image, replicas, resources, namespace, runtime endpoint bindings, and
secret-provider bindings. Use `EnvironmentOverlay` for provider endpoints that
vary by environment rather than editing the base logical definition; use
`EnvironmentConfig` when runtime, authentication, or audit fields also need
deployment-specific values.

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

When `error_policy: retry` is selected, retries are bounded by
`retry_max_attempts` (one retry by default), `retry_budget_seconds` (optional),
and the invocation deadline. `retry_backoff_seconds` is the base delay and is
doubled for each retry; `retry_jitter_seconds` adds a bounded random delay to
reduce synchronized retry storms. Defaults preserve the historical immediate
single retry:

```yaml
spec:
  runtime:
    error_policy: retry
    retry_max_attempts: 2
    retry_backoff_seconds: 0.25
    retry_jitter_seconds: 0.1
    retry_budget_seconds: 5
```

Retries are attempted only for failures before a non-read-only tool starts.
The runtime still lacks a circuit breaker and configurable error taxonomy;
those remain explicit backlog items.

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
