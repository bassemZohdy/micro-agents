# Runtimes

A Micro-Agent definition is runtime-neutral. A **runtime** binds it to an agent
framework behind the small `AgentRuntime` interface (SPI); the executable
bootstrap selects one by name from a **runtime registry**. Nothing in the
definition, core contracts or bootstrap depends on a particular framework.

```
 MicroAgentDefinition + MICRO_AGENT_* configuration
                  |
            bootstrap builds providers ──> RuntimeBuildContext
                  |
        RuntimeRegistry (name / alias / entry point)
                  |
   +--------------+---------------+------------------+
   |              |               |                  |
 custom       google-adk       langgraph        your runtime
 (reference)  (ADK adapter)    (StateGraph)     (entry point)
```

## Built-in runtimes

| Runtime | Select with | Extra | Orchestration |
|---|---|---|---|
| `custom` (aliases `adk`, `reference`) | default | none | project-owned bounded model/tool loop in `runtimes/adk` |
| `google-adk` | `MICRO_AGENT_RUNTIME=google-adk` | `adk` | Google ADK `LlmAgent` and `Runner` in `runtimes/google_adk` |
| `langgraph` | `MICRO_AGENT_RUNTIME=langgraph` | `langgraph` | LangGraph `StateGraph` in `runtimes/langgraph` |

> The package named `runtimes/adk` is the **custom** loop, not Google ADK. The
> name is historical ([ADR 0008](adr/0008-runtime-adapter-truthfulness.md));
> `runtimes/google_adk` is the real ADK adapter.

### What each runtime supports

| Capability | custom | google-adk | langgraph |
|---|---|---|---|
| Streaming | provider-dependent | provider-dependent | provider-dependent |
| Structured output | provider-dependent | provider-dependent | provider-dependent |
| Memory, MCP, knowledge, policy, audit, telemetry | yes | yes | yes |
| Idempotent side-effect operations | yes | yes | yes |
| Session persistence | yes | yes (ADK transcript layout) | yes |
| Checkpoint / resume | yes | yes | **no** |
| Durable approval continuations | yes (`ApprovalStore`) | yes (ADK native, in the session) | **no** |
| Native framework model path | no | yes (Gemini) | no |

`langgraph` fails closed where it lacks a feature: a tool whose side-effect
policy requires approval is **denied** rather than paused, `checkpoint_id` and
`continuation_id` requests are rejected, and bootstrap rejects
`MICRO_AGENT_APPROVAL_ENDPOINT` with that runtime instead of ignoring it.
Models are reached through the Micro-Agent provider SPI, so every built-in
provider (OpenAI-compatible, Azure OpenAI, Anthropic, Gemini, fake) works
unchanged; no LangChain chat model is involved.

### Choosing one

- Start with **custom**. It has no extra dependency and the fullest feature set.
- Pick **google-adk** to reuse ADK agents, its session model or native Gemini.
- Pick **langgraph** when your organisation standardises on LangGraph tooling
  (graph visualisation, LangSmith) or you intend to extend the graph.

The same definition and the same providers run on all three; see the
[conformance suite](#proving-a-runtime-conforms).

## The abstraction layer

Three pieces, all in `micro_agent.runtime`:

- **`AgentRuntime`** — the SPI: `capabilities`, `create`, `start`, `invoke`,
  optional `stream`, `stop`, `shutdown`, `health_probes`, `close`. No framework
  type crosses it.
- **`RuntimeBuildContext`** — everything the bootstrap built for the definition:
  model provider, session/memory/operation/approval stores, knowledge, MCP
  manager, policy, audit sink, telemetry and tool registry.
- **`RuntimeRegistration` / `RuntimeRegistry`** — a name, a factory
  `(RuntimeBuildContext) -> AgentRuntime`, aliases, and the few bootstrap-facing
  facts (`native_model`, `approval_store`, `approval_note`).

The bootstrap never mentions a framework. It resolves the registration, builds
the context, calls the factory. Built-ins are registered in
`micro_agent/config/runtimes.py`, the only place in `micro_agent` allowed to
import `runtimes.*`; each factory imports its adapter lazily so selecting one
runtime never needs another's dependency.

## Adding a runtime

1. Implement `AgentRuntime` (subclass `AdkRuntime` to inherit the shared
   governance — policy, argument validation, idempotency, audit, telemetry,
   retries, deadlines — and replace only the orchestration, as the LangGraph
   adapter does).
2. Write a factory that maps `RuntimeBuildContext` onto your configuration.
3. Register it, either in code:

   ```python
   from micro_agent.runtime import RuntimeRegistration, register_runtime

   register_runtime(
       RuntimeRegistration(
           name="my-runtime",
           factory=build_my_runtime,
           aliases=("mine",),
           approval_note="my-runtime does not map MICRO_AGENT_APPROVAL_ENDPOINT",
       )
   )
   ```

   or from a package, with no change to Micro-Agents:

   ```toml
   [project.entry-points."micro_agent.runtimes"]
   my-runtime = "my_package.runtime:REGISTRATION"
   ```

   An entry point may point at a `RuntimeRegistration` or at a zero-argument
   callable returning one. It is loaded only when selected.
4. Select it: `MICRO_AGENT_RUNTIME=my-runtime`.

Report capabilities truthfully, keep framework types out of the public
boundary, and reject (do not silently ignore) configuration you cannot honour.

## Proving a runtime conforms

`tests/test_runtime_conformance.py` runs one contract against every runtime:
capability matrix, identity without framework types, a simple invocation, session
history replayed to the model, a full tool loop, model failure surfacing, and
unhealthy-model startup failure. A new runtime joins by adding one factory to
that file.

`tests/test_dependency_rules.py` keeps the layering honest: `micro_agent` never
imports `runtimes` outside the composition root, optional frameworks are never
imported unguarded, and each adapter's fan-out into the framework is capped.

## Installing

```bash
python -m pip install -e ".[dev,adk]"        # Google ADK
python -m pip install -e ".[dev,langgraph]"  # LangGraph
```

Both extras are pinned. Neither is in the base install, the container image or
the default CI job.
