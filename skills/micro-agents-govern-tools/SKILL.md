---
name: micro-agents-govern-tools
description: Configure how a Micro-Agents agent is allowed to act, covering tool side-effect classification, native and plugin tools, MCP servers, deterministic policy rules, approval pause and resume, idempotency, checkpoint and resume, and retry, fallback, circuit-breaker and deadline settings. Use when adding a tool, restricting what an agent may do, handling approval_required responses, recovering a failed invocation, or tuning resilience.
license: Apache-2.0
compatibility: Needs the micro-agents package; the mcp, redis and postgres extras for MCP and shared state.
metadata:
  project: micro-agents
---

# Govern what an agent can do

These behaviors are enforced by the governance layer at the tool and model
boundaries, so they are identical on `custom`, `google-adk` and `langgraph`.

## 1. Classify every tool

```yaml
spec:
  dependencies:
    tools:
      - {name: profile,           source: native, side_effect: read_only}
      - {name: charge-card,       source: mcp,    side_effect: idempotent}
      - {name: send-notification, source: native, side_effect: unsafe}
```

| `side_effect` | Meaning | Consequence |
|---|---|---|
| `read_only` | writes no external state | subject to allow/deny policy; no approval, no operation-registry claim |
| `idempotent` | a stable `idempotency_key` makes a retry safe | operation registry de-duplicates; approval/policy gated |
| `unsafe` (default) | may have a non-repeatable effect | approval/policy gated; invocation retry is suppressed once it started |

A classification documents a contract; it does not make a tool idempotent. Pick
the strictest value that is true. Tool arguments are validated against the
tool's schema before execution on every runtime.

## 2. Provide the tool

Resolution order: programmatic `tool_registry` injection, then plugin packages,
then the built-in native registry. A plugin exposes an entry point:

```toml
[project.entry-points."micro_agent.tools"]
weather = "weather_tools:WeatherTool"     # a Tool subclass, or a zero-arg callable returning one
```

Tools are keyed by `metadata.name`; a name that resolves nowhere fails before the
runtime is created. For MCP, declare `mcp_servers` (see
`micro-agents-author-definition`) and install the `mcp` extra; discovered tools
surface to every runtime. Restrict a server with `allowed_capabilities`.

## 3. Restrict with policy

`spec.security.policy_refs` names policies resolved at bootstrap (an injected
`AgentPolicy`, a resolver, or `MICRO_AGENT_POLICY_STORE_ENDPOINT`; an
unresolved ref fails startup). Rules address `skill:<id>`, `tool:<name>`,
`mcp:<ref>`, `model:<ref>`, `side_effect:<name>` with actions `invoke`,
`connect`, `execute`, and optional `conditions` (`equals`, `in`, `contains`,
`matches`, `exists`, ...) over verified `tenant_id`, `user_id`, `roles`,
`caller_id`. **A matching deny beats any allow**; a rule whose condition field is
missing simply does not match. Denials are audited and surfaced as
authorization errors (HTTP 403 `authorization_denied`).

## 4. Approvals

When policy sets `approval_required`, a side-effect tool call pauses the
invocation instead of running or failing:

```json
{"status": "approval_required", "metadata": {"continuation_id": "...", "pending_tools": ["send-notification"]}}
```

Resume the same invocation with `metadata.continuation_id` and a decision
(a `continuation_id` without an `approval_decision` is rejected):

```bash
curl -X POST .../v1/invoke -H 'Content-Type: application/json' \
  -d '{"input":{},"continuation_id":"<id>","approval_decision":"approve"}'   # or "deny"
```

`approve` runs the pending tools (hard policy denials still apply); `deny` gives
the model the denial so it can answer. Continuations expire after five minutes
idle; an unknown or expired id is a stable 404. On `google-adk`, include the
original `session_id` on resume. Share pending approvals across replicas with
`MICRO_AGENT_APPROVAL_ENDPOINT=redis://...` (custom and langgraph).
`continuation_id` and `checkpoint_id` are mutually exclusive.

## 5. Idempotency

Set `MICRO_AGENT_IDEMPOTENCY_ENDPOINT` (Redis or PostgreSQL) so a retried or
replayed side-effect call returns the recorded result instead of acting twice,
across replicas. Keys are namespaced by verified tenant.

## 6. Checkpoint and resume

Checkpointing needs a `CheckpointStore`, which the bootstrap derives from the
session persistence (memory = process-local, SQLite = local restart, Redis or
PostgreSQL = shared). The checkpoint id is the invocation's `request_id`, so
callers that want recovery must send a stable `request_id`:

```bash
# resume a failed invocation: same id, empty input
curl -X POST .../v1/invoke -d '{"input":{},"checkpoint_id":"request-123"}'
```

Checkpoints are written only at replay-safe model-call boundaries. Before a tool
wave containing a non-read-only tool runs, the previous checkpoint is deleted,
so a crash mid-side-effect deliberately has nothing to replay. Treat the store
as sensitive session data. Unknown, expired, cross-tenant or foreign-agent ids
fail with `checkpoint_not_found`.

## 7. Resilience (`spec.runtime`)

```yaml
spec:
  runtime:
    timeout_seconds: 120
    error_policy: retry
    retry_max_attempts: 2
    retry_backoff_seconds: 0.25
    retry_jitter_seconds: 0.1
    retry_budget_seconds: 5
    circuit_breaker_failures: 5
    circuit_breaker_cooldown_seconds: 30
    max_concurrency: 8
    concurrency_policy: reject      # or wait
```

Retries run only before a non-read-only tool starts, are bounded by attempts,
budget and the invocation deadline, and use doubling backoff. The deadline is
shared by every nested model, tool/MCP, session and memory call and a retry
cannot reset it (HTTP 504 `deadline_exceeded`). `reject` returns 429
`invocation_overloaded`.

## Checklist before shipping a tool

- [ ] `side_effect` declared and accurate
- [ ] policy covers who may invoke it, with a deny for the risky cases
- [ ] write tools are `idempotent` with a key, or approval-gated
- [ ] shared idempotency/approval/session endpoints set for multi-replica
- [ ] a conformance-style test proves the behavior on more than one runtime

## References

- Configuration (policy, approvals, MCP, limits): https://bassemzohdy.github.io/micro-agents/CONFIGURATION/
- Checkpointing: https://bassemzohdy.github.io/micro-agents/CHECKPOINTING/
- Governance design: https://bassemzohdy.github.io/micro-agents/adr/0022-runtime-gap-filling-layer/
