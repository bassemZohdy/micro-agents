# HTTP API

The native HTTP API is versioned as `v1`. Native resource paths use the
`/v1/` prefix, the generated OpenAPI document is available at
`/v1/openapi.json`, and every response advertises
`X-Micro-Agent-API-Version: v1`. The old `/openapi.json` URL remains a
read-only compatibility alias; new clients should use the versioned URL.

## Endpoints

| Method and path | Purpose | Current limitation |
|---|---|---|
| `POST /v1/invoke` | invoke the agent | authentication is applied when configured/required; declared contracts are enforced |
| `GET /metrics` | Prometheus operational metrics | exposes the in-memory collector; configure an OpenTelemetry exporter for multi-replica production aggregation |
| `GET /health/live` | process liveness | always healthy unless changed programmatically |
| `GET /health/ready` | dependency readiness | returns 200 when ready and 503 when unhealthy; configured model, state, and declared MCP providers are probed before startup readiness |
| `GET /v1/capabilities` | runtime/skill metadata | reports the runtime capability matrix, not end-to-end readiness |
| `GET /v1/openapi.json` | versioned OpenAPI document | describes the native HTTP and A2A routes |
| `GET /v1/docs` | Swagger UI | interactive documentation for the versioned API |
| `GET /v1/redoc` | ReDoc | alternative documentation for the versioned API |
| `GET /.well-known/agent-card.json` | standard A2A agent card | served from the official SDK card model |
| `POST /` | A2A JSON-RPC `message/send` | available when `spec.interoperability.a2a.enabled` is true; non-streaming tasks only |

## Invoke request

```json
{
  "input": {
    "message": "hello"
  },
  "request_id": "request-123",
  "session_id": "session-456",
  "caller_metadata": {},
  "timeout_seconds": 15
}
```

`input` must currently be a JSON object. The definition's input contract is
enforced when parameters are declared: required fields, JSON-compatible types,
and unknown fields are rejected. `caller_metadata` is untrusted application
data and must not be used as authenticated caller identity.

Requests are limited to 1 MiB by default when `create_app()` is used. A
deployment gateway should apply the same limit and reject chunked requests
that exceed it; applications can choose a smaller limit with the
`max_request_bytes` factory argument.

### CORS

CORS is disabled by default. Deployments that serve a browser client can pass
an explicit allowlist to `create_app(cors_origins=[...])`, or set the
comma-separated `MICRO_AGENT_CORS_ORIGINS` environment variable for the
executable bootstrap. Entries must be absolute `http` or `https` origins;
`*` is allowed as the sole entry. Credentials are never enabled by this
default policy, so browser authentication should use a same-site gateway or a
deployment-specific middleware with an explicit credential policy.

Only `GET`, `POST`, and `OPTIONS` are allowed cross-origin. The API version and
retry headers are exposed to browser clients.

### Rate limiting

The framework does not choose a rate-limit algorithm or local counter. Pass a
deployment-owned `RateLimiter` implementation to
`create_app(rate_limiter=...)`. Its `check(request)` method may be synchronous
or asynchronous and returns either a boolean or `RateLimitDecision`:

```python
from micro_agent.interoperability import RateLimitDecision, create_app


class DistributedLimiter:
    async def check(self, request):
        allowed, remaining = await reserve_from_gateway(request)
        return RateLimitDecision(
            allowed=allowed,
            retry_after_seconds=5,
            limit=100,
            remaining=remaining,
        )


app = create_app(agent, rate_limiter=DistributedLimiter())
```

Rejected requests return HTTP 429 with `detail.code: rate_limited`, a
`Retry-After` header, and optional `X-RateLimit-Limit` /
`X-RateLimit-Remaining` headers. Limiter failures return the generic
`rate_limiter_unavailable` HTTP 503 contract. Health and discovery routes are
not rate-limited by this hook. Use a shared gateway or datastore for limits
across replicas.

## OpenAI-compatible model calls

When the selected model provider is OpenAI-compatible, the configured endpoint
is treated as a base URL and its path prefix is retained. An endpoint ending in
`/v1` therefore receives `GET /v1/models` during readiness and
`POST /v1/chat/completions` during invocation. Session-backed invocations retain
the complete conversation turn, including assistant `tool_calls` and matching
tool results, so later turns can replay the provider-required transcript.

`request_id` is optional. When it is omitted or empty, the service generates a
UUID and returns the same value in the response.

`timeout_seconds` is an optional positive end-to-end deadline for the
invocation. The runtime uses the shortest of the request deadline, the
definition's overall timeout, and each model/tool timeout. Cancellation of any
in-flight model, tool (including MCP adapters), session, or memory operation
releases its resources and propagates to that provider.

The current invoke wire format is JSON only. A request with
`Accept: text/event-stream` receives HTTP 406 and `detail.code:
streaming_unsupported` when the selected runtime advertises
`streaming: false`. The default and current built-in runtimes advertise
`streaming: false`; no streaming response endpoint is claimed until an adapter
implements it.

## Distributed operation idempotency

Tool declarations classify each operation as `read_only`, `idempotent`, or
`unsafe`. `read_only` tools bypass side-effect approval and idempotency claims;
the other classes retain policy enforcement and can use the registry below.
The default for legacy or undeclared tools is `unsafe`.

The custom runtime can use a shared Redis operation registry when
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT` is configured. Side-effect tools may include
an `idempotency_key` in their arguments. Reusing the same stable key causes a
retry on another replica to return the original result instead of executing the
tool again; while the first attempt is still running, the duplicate receives an
`operation is already in progress` result with `was_deduplicated: true`.
Claims are atomic and results expire with the registry TTL (one day by default).
Operation objects carry the mapped retry classification (`safe`, `idempotent`,
or `unsafe`) into registry and audit hooks for downstream dispatch decisions.
The custom runtime suppresses whole-invocation retries after a non-read-only
tool starts, so a later model failure cannot replay an unknown write outcome.
Before any side effect, `error_policy: retry` uses the definition's bounded
attempt count, exponential backoff, optional jitter, and retry wall-clock
budget; the default remains one immediate retry for compatibility.
Keys are tenant-scoped when a verified tenant identity is available (local or
unverified calls retain the legacy provider-wide namespace). Session and memory
records carry the same optional `tenant_id` boundary and a monotonically
increasing `version`. Providers raise `StateConflictError` when a non-zero
expected version is stale; a zero-version write keeps legacy unconditional
semantics. The Google ADK runtime rejects this binding until its idempotency
mapping is available.

## Invoke response

```json
{
  "output": {
    "content": "fake response",
    "tool_results": []
  },
  "request_id": "request-123",
  "session_id": "session-456",
  "status": "success",
  "error": null,
  "metadata": {}
}
```

Contract violations return HTTP 422 with a stable detail object:

```json
{
  "detail": {
    "code": "contract_validation_failed",
    "contract": "input",
    "errors": ["missing required field 'message'"]
  }
}
```

Oversized bodies return HTTP 413 with `code: request_too_large`. Runtime
failures use stable detail codes and deliberately generic messages:

| Condition | Status | Detail code |
|---|---:|---|
| authentication failure raised by an integration | 401 | `authentication_required` |
| authorization or policy denial | 403 | `authorization_denied` |
| invocation deadline exceeded | 504 | `deadline_exceeded` |
| required model, state, or other dependency unavailable | 503 | `dependency_unavailable` |
| unexpected runtime failure | 500 | `internal_error` |

When an authenticator is configured, `/v1/invoke` and an enabled A2A RPC route
verify the bearer credential before invoking the agent. Health and card routes
remain public. If the definition requires caller identity, app creation fails
without an authenticator. Exception text is not returned in any of these
responses.

An exhausted request or definition deadline returns HTTP 504:

```json
{
  "detail": {
    "code": "deadline_exceeded",
    "message": "Invocation deadline exceeded"
  }
}
```

## Invocation concurrency

Definitions can bound concurrent calls and choose overload behavior:

```yaml
spec:
  runtime:
    max_concurrency: 8
    concurrency_policy: reject # or wait (the default)
```

`reject` returns HTTP 429 (`code: invocation_overloaded`) before model
invocation and includes `Retry-After: 1`. `wait` queues the caller until a
slot is available. Cancellation releases a queued slot. An invocation deadline
is shared by every nested model, tool/MCP, session, and memory operation, so a
retry cannot reset the caller's remaining budget.

## Health behavior

Liveness answers whether the process should be restarted. Readiness answers
whether the instance can serve traffic. The readiness endpoint returns HTTP
503 with `"status":"unhealthy"` when a required dependency fails its probe.

## Operational metrics

`GET /metrics` is a public scrape endpoint that returns Prometheus text format.
Counter points are accumulated per label set and other points expose their
latest value. The lightweight collector is intended for local/single-process
scraping; configure an OpenTelemetry metrics provider/exporter for durable
aggregation across replicas. Model usage follows the `model_tokens_total`
`token_type` convention (`prompt`, `completion`, `total`), and optional pricing
produces `model_cost_usd_total`.

## A2A

The standard card route is:

```text
GET /.well-known/agent-card.json
```

When enabled in the definition, the official SDK also mounts JSON-RPC at `/`
and handles `message/send` with submitted → working → completed/failed task
states. Streaming, push notifications, durable task state, and cancellation
are not yet implemented. Requests may declare `x-a2a-version`; unsupported
versions receive a stable 400 response.
