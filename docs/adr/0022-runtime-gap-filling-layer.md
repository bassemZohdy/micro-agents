# ADR 0022: A project-owned layer that fills runtime capability gaps

Date: 2026-11-08 · Status: Accepted (Phase 0 of the plan in `TODO.md`)

## Context

[ADR 0021](0021-runtime-registry-and-langgraph-adapter.md) made frameworks
selectable, and it exposed the next problem: the runtimes are not equivalent.

| Gap | custom | google-adk | langgraph |
|---|---|---|---|
| Durable approval continuations through `ApprovalStore` | yes | no (ADK-native, in session) | no (fails closed) |
| Checkpoint / resume through `CheckpointStore` | yes | yes (ADK-specific) | no |
| Session transcript layout | Micro-Agent | ADK | Micro-Agent |
| Governance without inheriting the reference runtime | n/a | partial | no |

A definition that pauses for approval on one runtime and denies the tool on
another is not runtime-neutral. Reimplementing each gap inside each adapter
duplicates security-sensitive logic and lets adapters drift.

## Decision

Micro-Agents owns the gap-filling semantics **once**, in a layer between the
bootstrap and the framework. An adapter supplies orchestration only. The layer
works at boundaries Micro-Agents already controls and never reaches into
framework internals.

Three mechanisms, delivered in later phases:

1. **Governed tools** (`GovernedTool`). Policy, argument validation, idempotent
   operations, audit, telemetry and the approval gate run at the tool boundary,
   whichever framework invokes the tool. Frameworks receive wrapped tools and
   cannot bypass governance. An approval-needing tool raises a neutral
   `ApprovalRequired`; the layer persists the request in the `ApprovalStore`.
2. **Governed model provider** (`GovernedModelProvider`). Retry, fallback,
   circuit breaker, deadline, usage accounting and redaction wrap the provider
   SPI, so adapters that use it get identical behavior.
3. **Runtime decorator** (`CheckpointingRuntime`). For a runtime without native
   checkpointing it snapshots neutral invocation state at tool-round boundaries
   in the `CheckpointStore` and replays it into a fresh framework invocation on
   resume. The registry wraps a runtime only for capabilities it lacks
   (`RuntimeRegistration.provides`).

**Reporting.** `RuntimeCapabilities` distinguishes where a capability comes
from. A boolean stays true when the capability is effectively available, so
`supports()` and `as_dict()` are unchanged. A new `provided` set names the
capabilities that exist only because the layer supplies them, and
`source(name)` / `sources()` return `native`, `provided` or `none`. The first
step of this ADR (Phase 0) ships that model and nothing else: no runtime
reports `provided` yet.

**Uniform guarantees.** `micro_agent.runtime.guarantees.UNIFORM_GUARANTEES` is
the single list of behaviors every runtime must show identically. Each entry is
`covered` (it names the conformance cases that prove it) or `planned` (it names
the plan phase that delivers it). A test requires every covered guarantee to
point at an existing conformance case and every conformance case to belong to a
guarantee, so the list and the suite cannot drift. A capability reported
`provided` must pass the same cases as `native`.

**Decision on ADK approvals (Phase 2).** ADK runs its own tool loop and owns its
resumable session, so its approval continuation stays ADK-native. Its tools run
through the shared `ToolGovernor` (policy, validation, idempotency, timeouts,
telemetry, audit); only the confirmation handshake is ADK's. The conformance
suite proves the approval contract (pause, approve, deny, unknown continuation)
on every runtime, so the difference is the storage location, not behavior.

**Stays framework-native.** The layer does not replace what a framework owns by
design: ADK's internal session transcript and its native confirmation flow
remain available (native approvals become an opt-in for ADK in Phase 2), and
the layer does not rewrite how a framework schedules its own steps.

## Rejected alternative

Reimplementing checkpoints and approvals separately in each adapter (for
example LangGraph's checkpointer and interrupts). It fits one framework and
leaves the others behind, doubles the security-sensitive code, and makes the
guarantees depend on framework internals that change between releases.

## Consequences

- A definition behaves the same on every runtime for the guaranteed behaviors;
  differences are reported, not hidden.
- New adapters opt into the layer instead of re-deriving governance.
- Checkpoints resume at tool-round boundaries. They cannot resume inside one
  model call or a streaming response, and the layer says so rather than claim it.
- Resumed side effects depend on stable operation keys across frameworks; the
  key derivation is part of the neutral governance core (Phase 1).
- Phase 1 removes the current `LangGraphRuntime` → `AdkRuntime` inheritance by
  extracting that core.
