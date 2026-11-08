# ADR 0021: Runtime registry and a LangGraph adapter

Date: 2026-11-08 · Status: Accepted

## Context

The runtime SPI (`AgentRuntime`) existed, but the bootstrap did not respect it:
`build_runtime` branched on `runtime_name == "google-adk"`, built that adapter's
configuration inline, and imported the custom runtime at module level. Adding a
second framework meant editing the bootstrap, and Google ADK looked like a
structural dependency rather than one implementation of the SPI.

## Decision

**Registry.** Add `RuntimeRegistry`, `RuntimeRegistration`, `RuntimeBuildContext`
and `register_runtime` to `micro_agent.runtime`. The bootstrap builds every
provider once into a `RuntimeBuildContext` and asks the registry for a factory.
The few things that differed per runtime become declarative fields on the
registration (`native_model`, `approval_store`, `approval_note`) instead of
`if` branches. Built-ins are registered in `micro_agent/config/runtimes.py`,
which stays the only place in `micro_agent` that imports `runtimes.*` (lazily).
Third parties register in code or through the `micro_agent.runtimes`
entry-point group, loaded only when selected.

**LangGraph adapter.** Add `runtimes/langgraph` whose invocation is a real
`StateGraph` — `prepare → model → (tools → model)* → finalize` with a bounded
loop edge. It subclasses the reference runtime to reuse governance (policy,
argument validation, idempotent operations, audit, telemetry, retry, fallback,
deadlines, circuit breaking) and replaces only orchestration. Models are called
through the Micro-Agent provider SPI, so no LangChain model abstraction is
involved and every built-in provider works.

**Honest scope.** The adapter does not implement checkpoint/resume or approval
continuations. It reports `checkpointing: false`, rejects `checkpoint_id` and
`continuation_id`, denies tools whose policy requires approval (fail closed),
and bootstrap rejects `MICRO_AGENT_APPROVAL_ENDPOINT` for it. Wiring LangGraph's
own checkpointer to `CheckpointStore` is possible later and is not claimed now.

**Conformance.** Add `tests/test_runtime_conformance.py`, one parametrized
contract run against the custom, Google ADK and LangGraph runtimes, and extend
the dependency-rule tests with a coupling cap for the new adapter.

## Consequences

- Google ADK is now demonstrably one adapter among three; removing or freezing
  it later touches its own package and one registration.
- A new framework adapter needs a factory and a registration, not a bootstrap
  change.
- *(Superseded by ADR 0022 Phase 1: both runtimes now subclass the neutral
  `micro_agent.governance.GovernedRuntime`.)* `LangGraphRuntime` inherited from `AdkRuntime` (the reference loop, whose
  package name is historical). That is a deliberate reuse trade-off: the
  adapter depends on the reference loop's private helpers. Extracting the shared
  governance into a neutral base class is the natural follow-up if a third
  adapter wants it.
- `langgraph==1.2.20` is a new optional pinned extra with its own CI job
  (`langgraph`). It is excluded from default coverage like the ADK adapter. The
  job is not in the required-checks ruleset until the owner adds it.
- The ADK session transcript layout differs from the others, so the conformance
  suite asserts that history is replayed to the model, not how it is stored.
