# HTTP API

The current API is pre-release and unversioned beyond the `/v1` path.

## Endpoints

| Method and path | Purpose | Current limitation |
|---|---|---|
| `POST /v1/invoke` | invoke the agent | no authentication or contract enforcement |
| `GET /health/live` | process liveness | always healthy unless changed programmatically |
| `GET /health/ready` | dependency readiness | returns 200 when ready and 503 when unhealthy |
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

`request_id` is optional. When it is omitted or empty, the service generates a
UUID and returns the same value in the response.

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
whether the instance can serve traffic. The readiness endpoint returns HTTP
503 with `"status":"unhealthy"` when a required dependency fails its probe.

## A2A

The existing discovery route is preliminary. The target A2A v1.0.1 route is:

```text
GET /.well-known/agent-card.json
```

The target card uses `supportedInterfaces`; full A2A also requires a standard
message/task binding. See [Standards](STANDARDS.md).
