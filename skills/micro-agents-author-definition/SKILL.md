---
name: micro-agents-author-definition
description: Write, validate and review a Micro-Agents agent definition (YAML, apiVersion microagents.io/v1alpha1). Use when creating or editing a MicroAgent file, choosing model, tool, MCP, session, memory, knowledge, runtime-policy or security fields, or fixing a definition that fails to load or start.
license: Apache-2.0
compatibility: Needs Python 3.11+ and the micro-agents package from a source checkout (pip install -e .) or a GitHub Release wheel to validate.
metadata:
  project: micro-agents
---

# Author a Micro-Agents definition

A definition is the portable description of one agent. It runs unchanged on every
runtime (`custom`, `google-adk`, `langgraph`), so it must never name a runtime,
a secret value or an environment-specific URL.

## Workflow

1. Start from the closest example: `examples/support-desk-agent.yaml` (small,
   boots as written) or `examples/residency-renewal.yaml` (every section).
2. Fill `metadata`, `spec.behavior`, then only the `spec.dependencies` you need.
3. Validate (see below) before running anything.
4. Keep deployment concerns out of the file (see "What does not belong").

## Skeleton

```yaml
apiVersion: microagents.io/v1alpha1
kind: MicroAgent
metadata:
  name: support-desk-agent        # required
  version: 0.1.0                  # required
  description: One sentence.
spec:
  behavior:
    instructions: |               # required
      You are a support desk assistant. Keep answers short.
    input_contract:
      parameters:
        - {name: question, type: string, required: true}
  dependencies:
    model:
      ref: reasoning-model        # logical alias; required
      provider: fake              # explicit; see "Model" below
    tools:
      - name: echo
        source: native
        side_effect: read_only    # read_only | idempotent | unsafe (default)
    session:
      persistence: memory
      ttl_seconds: 3600
```

## Field guide

| Section | Notes |
|---|---|
| `metadata` | `name` and `version` required; `labels`, `annotations`, `description` optional. |
| `spec.behavior` | `instructions` required. `input_contract` / `output_contract` take `parameters` (`name`, `type`, `required`, `default`, `description`). Declared contracts are enforced at the invocation boundary: missing required fields, wrong JSON types and unknown fields are rejected. An empty contract is unconstrained. |
| `dependencies.model` | `ref` required. Optional `provider`, `model_id`, `endpoint`, `credential_ref`, `generation` (temperature, max_tokens, ...), `timeout_seconds`. |
| `dependencies.tools[]` | `name` required; `source` (`native`, `mcp`), `timeout_seconds`, `side_effect`. Unresolvable tools fail before the runtime is created. |
| `dependencies.mcp_servers[]` | `ref` required; `transport` (`streamable-http`, `stdio`; `sse` is legacy), `endpoint` (https, or loopback) or `command`+`args` for stdio, `credential_ref`, `allowed_capabilities`, `timeout_seconds`. Needs the `mcp` extra. |
| `dependencies.session` | `persistence`: `none` (default), `memory`, `sqlite`, `external`; `ttl_seconds`. |
| `dependencies.memory` / `knowledge[]` | `memory`: `ref`, `scope`. `knowledge`: `ref`, `source_type`, `version`, `max_results` (5), `max_context_characters` (4000). Knowledge is read-only reference data, never memory. |
| `dependencies.skills[]` | Agent capabilities advertised for discovery and A2A (`id`, `name`, `description`, `tags`). **Not** Agent Skills / `SKILL.md` files. |
| `spec.runtime` | `timeout_seconds`, `max_iterations`, `max_concurrency`, `concurrency_policy` (`wait`/`reject`), `shutdown_timeout_seconds`, `error_policy` (`fail`/`retry`), `retry_max_attempts`, `retry_backoff_seconds`, `retry_jitter_seconds`, `retry_budget_seconds`, `circuit_breaker_failures`, `circuit_breaker_cooldown_seconds`, `capabilities` (required runtime features, for example `memory`, `mcp`). |
| `spec.interoperability.a2a` | `enabled`, `endpoint`, `protocol_version`. |
| `spec.security` | `credential_refs`, `identity_requirements.require_caller_identity`, `policy_refs`. |

## Model

A bare model reference is rejected at startup so production never silently uses
the fake provider. Set one of: `provider: fake` (offline/CI only),
`provider: openai-compatible` + `endpoint`, `anthropic`, `azure-openai` or
`gemini`; or keep a logical `ref` and let deployment configuration resolve it
(`MICRO_AGENT_MODEL_*` variables or a model catalog). Credentials are always
`credential_ref` names resolved from the environment or a secret manager.

## Tool side effects (decide deliberately)

- `read_only`: no external writes; skips approval and operation-registry claims.
- `idempotent`: a stable `idempotency_key` makes a retry safe.
- `unsafe` (default): may have a non-repeatable effect; approval/policy gated and
  never retried once started. Prefer the strictest accurate value.

## Validate

```bash
python -c "from micro_agent.definition import load_definition_from_file as l; print(l('agent.yaml').metadata)"
python -m micro_agent --definition agent.yaml --host 127.0.0.1 --port 8080   # boots and probes dependencies
```

The JSON Schema is `docs/schemas/micro-agent-v1alpha1.json` (point an editor's
YAML language server at it). `apiVersion: microagents.io/v1beta1` is also
accepted with camelCase wire names (`inputContract`, `mcpServers`,
`credentialRef`); the loader translates it. Unknown fields and unsupported
versions fail closed.

## What does not belong in a definition

- A runtime name: select it with `MICRO_AGENT_RUNTIME` (skill `micro-agents-choose-runtime`).
- Secret values, tokens, passwords: use `credential_ref` / `credential_refs`.
- Environment-specific endpoints (staging vs production model or MCP URLs): bind
  them with `EnvironmentOverlay` or `MICRO_AGENT_*` variables so one definition
  is promoted unchanged.
- Replica counts, resources, images: deployment manifests own those.

## Common failures

| Symptom | Cause / fix |
|---|---|
| `DefinitionError ... Extra inputs are not permitted` | Unknown key or a typo (the error names the path, for example `spec -> behavior -> bogus`); compare with the schema. |
| Startup rejects the model | Bare `ref` with no `provider`/`endpoint`; set one explicitly. |
| Startup rejects a tool | Name does not resolve in the tool registry; add a plugin tool or fix `source`. |
| Startup rejects `session.persistence: external` | No matching `MICRO_AGENT_SESSION_ENDPOINT` (redis/postgres extra installed?). |
| Not ready, dependency unhealthy | A probed model, store, knowledge source or MCP server is unreachable; fix the dependency, do not remove it. |
| Invocation returns a contract error | Request `input` does not satisfy `input_contract`. |

## References

- Configuration reference: https://bassemzohdy.github.io/micro-agents/CONFIGURATION/
- Definition JSON Schema: `docs/schemas/micro-agent-v1alpha1.json` in the repository
- Decision record: https://bassemzohdy.github.io/micro-agents/adr/0001-runtime-neutral-definition/
