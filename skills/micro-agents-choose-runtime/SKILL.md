---
name: micro-agents-choose-runtime
description: Choose and switch the Micro-Agents runtime (custom reference loop, google-adk, langgraph) with MICRO_AGENT_RUNTIME, understand which capabilities are native versus provided by the governance layer, and move sessions or checkpoints between runtimes safely. Use when deciding which runtime to deploy, enabling the adk or langgraph extra, or investigating a behavior difference between runtimes.
license: Apache-2.0
compatibility: Needs the micro-agents package; the adk and langgraph extras only for those runtimes.
metadata:
  project: micro-agents
---

# Choose a runtime

The runtime is a **deployment** choice. The definition is identical for all of
them, and a conformance suite proves they behave the same for the 17 uniform
guarantees (policy, argument validation, idempotent side effects, approvals,
retry/fallback/circuit breaking, deadlines, usage accounting, checkpoint/resume,
session portability, audit and telemetry, and more).

| Runtime | Select | Extra | Orchestration |
|---|---|---|---|
| `custom` (aliases `adk`, `reference`) | default | none | project-owned bounded model/tool loop |
| `google-adk` (alias `google_adk`) | `MICRO_AGENT_RUNTIME=google-adk` | `adk` | Google ADK `LlmAgent` + `Runner` |
| `langgraph` | `MICRO_AGENT_RUNTIME=langgraph` | `langgraph` | LangGraph `StateGraph` |

Third-party runtimes register through the `micro_agent.runtimes` entry-point
group and are selected by their registered name (skill
`micro-agents-add-runtime-adapter`).

## Decide

- Start with **custom**: no extra dependency, fullest feature set.
- **google-adk** to reuse ADK agents/session model or the native Gemini path.
- **langgraph** when your organisation standardises on LangGraph tooling
  (graph visualisation, LangSmith) or you will extend the graph.

```bash
python -m pip install -e ".[dev,adk]"          # or ".[dev,langgraph]"
export MICRO_AGENT_RUNTIME=langgraph
python -m micro_agent --definition agent.yaml
```

An unknown name fails startup and lists the registered runtimes. Do not put the
runtime in the definition.

## Native versus provided

`GET /v1/capabilities` reports the runtime capability matrix. Each capability is
`native` (framework or reference loop supplies it), `provided` (the Micro-Agents
governance layer supplies it because the framework does not) or `none`.
Currently the only provided capability is **checkpoint/resume on `langgraph`**.
A2A is served by the HTTP layer for every runtime, so the runtime-level flag is
`no` everywhere. The authoritative table is generated into `docs/RUNTIMES.md`
(`python tools/runtime_capabilities.py --check` fails if it is stale); read it
rather than trusting a copy.

Remaining intentional differences:

| Difference | custom | google-adk | langgraph |
|---|---|---|---|
| Approval continuations | `ApprovalStore` (`MICRO_AGENT_APPROVAL_ENDPOINT` works) | ADK native, in the session; resume needs the original `session_id`; endpoint is rejected | `ApprovalStore` (endpoint works) |
| Native framework model path | no | yes (Gemini) | no |
| Checkpoint state | neutral schema | ADK-specific (events + state) | neutral schema |

Model providers (OpenAI-compatible, Azure OpenAI, Anthropic, Gemini, fake) are
reached through the Micro-Agents provider SPI, so they work on all runtimes.

## Switching with state in flight

- **Sessions**: every runtime stores the same transcript (`user`, `assistant`
  with optional `tool_calls`, `tool`) under the plain session id. Changing
  `MICRO_AGENT_RUNTIME` needs no export or conversion. ADK keeps richer native
  events in session metadata and rebuilds them from the transcript if another
  runtime appended. Older ADK-prefixed sessions migrate on first read.
- **Checkpoints**: `custom` and `langgraph` share a neutral, versioned schema, so
  a checkpoint from one resumes on the other. An ADK checkpoint carries ADK
  session events and is refused by the neutral runtimes (and the reverse). Drain
  or let in-flight resumable work finish before moving between ADK and the others.
- **Approvals**: pending approvals live in the runtime's own mechanism; resolve
  them before switching.
- Session ids are shared across agents in one store; use distinct ids per agent.

## Verify a switch

```bash
pytest -q tests/test_runtime_conformance.py tests/test_session_portability.py   # needs both extras
python tools/runtime_capabilities.py --check
```

CI runs these in the `runtime-parity` job. If behavior differs between runtimes
for a definition, that is a bug in the layer or an adapter: reproduce it as a
conformance case (skill `micro-agents-contribute`) rather than working around it
in the definition.

## References

- Runtimes: https://bassemzohdy.github.io/micro-agents/RUNTIMES/
- Checkpointing: https://bassemzohdy.github.io/micro-agents/CHECKPOINTING/
- ADR 0021 and 0022: https://bassemzohdy.github.io/micro-agents/adr/0022-runtime-gap-filling-layer/
