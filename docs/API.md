# HTTP API

The current API is pre-release and unversioned beyond the `/v1` path.

## Endpoints

| Method and path | Purpose | Current limitation |
|---|---|---|
| `POST /v1/invoke` | invoke the agent | no authentication; declared contracts are enforced |
| `GET /health/live` | process liveness | always healthy unless changed programmatically |
| `GET /health/ready` | dependency readiness | returns 200 when ready and 503 when unhealthy; configured model, state, and declared MCP providers are probed before startup readiness |
| `GET /v1/capabilities` | runtime/skill metadata | reports the runtime capability matrix, not end-to-end readiness |
| `GET /.well-known/agent.json` | preliminary card | not the A2A v1 standard path or card |

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

`request_id` is optional. When it is omitted or empty, the service generates a
UUID and returns the same value in the response.

`timeout_seconds` is an optional positive end-to-end deadline for the
invocation. The runtime uses the shortest of the request deadline, the
definition's overall timeout, and each model/tool timeout. Cancellation of any
in-flight model, tool (including MCP adapters), session, or memory operation
releases its resources and propagates to that provider.

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

The 401 mapping is ready for an authentication middleware integration; the
default application does not authenticate callers yet. Exception text is not
returned in any of these responses.

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

The existing discovery route is preliminary. The target A2A v1.0.1 route is:

```text
GET /.well-known/agent-card.json
```

The target card uses `supportedInterfaces`; full A2A also requires a standard
message/task binding. See [Standards](STANDARDS.md).
