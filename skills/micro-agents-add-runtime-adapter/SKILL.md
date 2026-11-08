---
name: micro-agents-add-runtime-adapter
description: Add a new Micro-Agents runtime (an adapter for an agent framework) and make it behave identically to the built-in ones by opting into the governance layer. Use when integrating another agent framework, registering a runtime through the micro_agent.runtimes entry point, or making a runtime pass the 17-guarantee conformance suite.
license: Apache-2.0
compatibility: Needs a source checkout of micro-agents with the dev extra; the framework being adapted is installed as an optional extra.
metadata:
  project: micro-agents
---

# Add a runtime adapter

A runtime binds a runtime-neutral definition to one framework behind the small
`AgentRuntime` interface. **Adapters supply orchestration only.** Policy,
argument validation, idempotency, approvals, retry/fallback/circuit breaking,
deadlines, usage accounting, checkpoints and sessions come from the governance
layer in `micro_agent.governance`, applied at boundaries Micro-Agents controls
(tools, model provider, stores, request/response). Never patch a framework's
internals to get a behavior.

## Pick the path by who owns the loop

**You own the loop** (like `runtimes/custom` and `runtimes/langgraph`). Subclass
`micro_agent.governance.GovernedRuntime` and implement `_invoke_inner`. From your
orchestration call:

- `self._execute_tools(...)` for each tool wave (policy, validation,
  idempotency, approvals, timeouts, audit, telemetry);
- `agent._internal["model_provider"].generate(...)`, a `GovernedModelProvider`
  (deadline, span, latency and usage telemetry, streaming deltas);
- `_load_checkpoint` / `_save_checkpoint` / `_delete_checkpoint` at tool-round
  boundaries, and `_approval_needed` / `_save_pending_approval` /
  `_replay_pending_approval` to pause and resume.

Retry, fallback, circuit breaking and the deadline wrap `_invoke_inner` for you.

**The framework owns the loop** (like `runtimes/google_adk`). Compose the same
pieces at your boundaries: give the framework `GovernedTool`-wrapped tools (or a
`ToolGovernor` call inside your tool shim), bridge its model calls through
`GovernedModelProvider`, run each invocation through `InvocationGovernor.run`
with the tool and model scopes bound, and store sessions in the portable
transcript layout. Anything the framework keeps natively must still pass the same
conformance case.

## Steps

1. **Implement** `AgentRuntime` in `runtimes/<name>/` (package name with
   underscores). Keep framework types out of the public boundary; reject (do not
   silently ignore) configuration you cannot honour; report
   `RuntimeCapabilities` truthfully.
2. **Factory**: a function mapping `RuntimeBuildContext` (`definition`,
   `model_provider`, `session_provider`, `memory_provider`, `operation_registry`
   for idempotency, `approval_store`, `knowledge_provider`, `mcp_manager`,
   `policy`, `audit`, `telemetry`, `tool_registry`, `extra`) onto your
   configuration. Checkpointing follows the session provider.
3. **Register**, in code or with no change to Micro-Agents:

   ```python
   from micro_agent.runtime import RuntimeRegistration, register_runtime

   register_runtime(RuntimeRegistration(
       name="my-runtime", factory=build_my_runtime, aliases=("mine",),
       provides=("checkpointing",),         # capabilities the governance layer supplies
       approval_note="my-runtime does not map MICRO_AGENT_APPROVAL_ENDPOINT",
   ))
   ```

   ```toml
   [project.entry-points."micro_agent.runtimes"]
   my-runtime = "my_package.runtime:REGISTRATION"   # a RuntimeRegistration, or a zero-arg callable returning one
   ```

   An entry point is loaded only when selected. Select with
   `MICRO_AGENT_RUNTIME=my-runtime`.
4. **Declare what the layer fills** in `RuntimeRegistration.provides`; the
   capability then reports `provided` instead of `native`.
5. **Prove conformance**: add one factory to `tests/test_runtime_conformance.py`
   and `tests/test_session_portability.py` (guarded by `importorskip` for the
   optional framework). Every guarantee in
   `micro_agent.runtime.guarantees.UNIFORM_GUARANTEES` must pass; a `provided`
   capability runs the same cases as a `native` one.
6. **Docs**: add the runtime to the `RUNTIMES` tuple in
   `tools/runtime_capabilities.py`, then
   `python tools/runtime_capabilities.py --write` and commit the regenerated
   table in `docs/RUNTIMES.md`.
7. **Packaging**: add an optional extra in `pyproject.toml` (pinned), and a CI
   job (see the `adk` and `langgraph` jobs; the `runtime-parity` job installs
   all adapters together).
8. **Layering**: keep the adapter's imports inside its coupling cap in
   `tests/test_dependency_rules.py`. Only `micro_agent/config/runtimes.py` may
   import `runtimes.*`; adapters never import each other; optional frameworks are
   never imported unguarded.

## Verify

```bash
pytest -q tests/test_runtime_conformance.py tests/test_session_portability.py \
          tests/test_runtime_guarantees.py tests/test_runtime_gaps.py tests/test_dependency_rules.py
python tools/runtime_capabilities.py --check
ruff check . && ruff format --check . && mypy micro_agent cloud runtimes
```

Do not weaken a guarantee, skip a conformance case or add a per-runtime
exception to make an adapter pass. If a guarantee cannot be met at a boundary
you control, fail closed (reject the request or configuration) and document the
gap in the capability table.

## References

- Adding a runtime and opting in: https://bassemzohdy.github.io/micro-agents/RUNTIMES/
- Registry and LangGraph adapter: https://bassemzohdy.github.io/micro-agents/adr/0021-runtime-registry-and-langgraph-adapter/
- Gap-filling layer: https://bassemzohdy.github.io/micro-agents/adr/0022-runtime-gap-filling-layer/
