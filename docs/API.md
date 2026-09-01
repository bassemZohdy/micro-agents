# HTTP API

The current API is pre-release and unversioned beyond the `/v1` path.

## Endpoints

| Method and path | Purpose | Current limitation |
|---|---|---|
| `POST /v1/invoke` | invoke the agent | authentication is applied when configured/required; declared contracts are enforced |
| `GET /health/live` | process liveness | always healthy unless changed programmatically |
| `GET /health/ready` | dependency readiness | returns 200 when ready and 503 when unhealthy; configured model, state, and declared MCP providers are probed before startup readiness |
| `GET /v1/capabilities` | runtime/skill metadata | reports the runtime capability matrix, not end-to-end readiness |
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

## Distributed operation idempotency

The custom runtime can use a shared Redis operation registry when
`MICRO_AGENT_IDEMPOTENCY_ENDPOINT` is configured. Side-effect tools may include
an `idempotency_key` in their arguments. Reusing the same stable key causes a
retry on another replica to return the original result instead of executing the
tool again; while the first attempt is still running, the duplicate receives an
`operation is already in progress` result with `was_deduplicated: true`.
Claims are atomic and results expire with the registry TTL (one day by default).
Keys are currently provider-wide rather than tenant-isolated, and optimistic
versioning is not implemented. The Google ADK runtime rejects this binding
until its idempotency mapping is available.

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
