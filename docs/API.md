# HTTP API

The current API is pre-release and unversioned beyond the `/v1` path.

## Endpoints

| Method and path | Purpose | Current limitation |
|---|---|---|
| `POST /v1/invoke` | invoke the agent | no authentication or contract enforcement |
| `GET /health/live` | process liveness | always healthy unless changed programmatically |
| `GET /health/ready` | dependency readiness | returns HTTP 200 even when unhealthy |
| `GET /v1/capabilities` | runtime/skill metadata | reports runtime flags, not end-to-end readiness |
| `GET /.well-known/agent.json` | preliminary card | not the A2A v1 standard path or card |

## Invoke request

```json
{
  "input": {
    "message": "hello"
  },
  "request_id": "request-123",
  "session_id": "session-456",
  "caller_metadata": {}
}
```

`input` must currently be a JSON object. The definition's input contract is
not enforced. `caller_metadata` is untrusted application data and must not be
used as authenticated caller identity.

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

Error-to-HTTP-status mapping is not yet standardized. Runtime exceptions can
surface as generic HTTP 500 responses.

## Health behavior

Liveness answers whether the process should be restarted. Readiness answers
whether the instance can serve traffic. A production readiness endpoint must
return a non-2xx response—normally 503—when a required dependency is
unhealthy. The current implementation returns a body with
`"status":"unhealthy"` but still uses HTTP 200.

## A2A

The existing discovery route is preliminary. The target A2A v1.0.1 route is:

```text
GET /.well-known/agent-card.json
```

The target card uses `supportedInterfaces`; full A2A also requires a standard
message/task binding. See [Standards](STANDARDS.md).

