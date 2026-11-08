# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Gemini CLI, Cursor, Copilot
and any other harness that reads `AGENTS.md`). Humans should start at
[README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## What this project is

Micro-Agents is a Python framework for independently deployable AI agents. An
agent is a **portable YAML definition** (`apiVersion: microagents.io/v1alpha1`,
`kind: MicroAgent`) that runs unchanged on interchangeable **runtimes**: the
reference loop (`custom`), Google ADK (`google-adk`) and LangGraph
(`langgraph`). The runtime is a deployment choice, never part of the
definition.

## Skills

Task-specific playbooks live in [`skills/`](skills/), one folder per skill, in
the open Agent Skills format (`SKILL.md` with `name` and `description`
frontmatter). Load the one that matches your task instead of guessing:

| Skill | Use it to |
|---|---|
| `micro-agents-author-definition` | write, validate and review an agent definition YAML |
| `micro-agents-run-and-deploy` | run the service, call the HTTP API, configure state, containers, Kubernetes |
| `micro-agents-choose-runtime` | pick `custom`, `google-adk` or `langgraph`, switch safely, read the gap table |
| `micro-agents-govern-tools` | classify tool side effects, policy, approvals, MCP, checkpoints, resilience |
| `micro-agents-add-runtime-adapter` | add a runtime and opt it into the governance layer |
| `micro-agents-contribute` | change this repository: gates, tests, docs, schema, changelog |

## Repository map

| Path | Contents |
|---|---|
| `micro_agent/` | definition models, bootstrap/config, governance layer, tools, state, interoperability (HTTP, A2A, MCP) |
| `micro_agent/governance/` | framework-free layer that makes behaviour identical across runtimes |
| `runtimes/custom`, `runtimes/google_adk`, `runtimes/langgraph` | runtime adapters (orchestration only) |
| `cloud/` | optional control plane (registry, config, gateway, observability) |
| `docs/` | reference docs, ADRs (`docs/adr/`), generated JSON schemas (`docs/schemas/`) |
| `examples/` | executable definitions that boot with the fake model provider |
| `tools/` | repository tooling (`validate_release.py`, `runtime_capabilities.py`, `validate_skills.py`) |
| `tests/` | pytest suite; markers `integration`, `e2e`, `adk`, `langgraph`, `otel`, `postgres` |

## Commands

```bash
python -m pip install -e ".[dev]"                 # add ,adk,langgraph,redis,postgres as needed
python -m micro_agent --definition examples/support-desk-agent.yaml --port 8080

ruff check . && ruff format --check .
mypy micro_agent cloud runtimes                   # strict
pytest -m "not integration and not e2e and not otel"
python -m micro_agent.definition.schema           # regenerate docs/schemas/ after model changes
python tools/runtime_capabilities.py --check      # generated capability table in docs/RUNTIMES.md
python tools/validate_skills.py                   # skills and plugin manifests
mkdocs build --strict
```

## Rules that are easy to break

- **Gaps are filled at boundaries Micro-Agents controls** (tools, model
  provider, stores, request/response). Never reach into a framework's internals
  to make it behave; see [ADR 0022](docs/adr/0022-runtime-gap-filling-layer.md).
- **Only `micro_agent/config/runtimes.py` imports `runtimes.*`**, and adapters
  never import each other. `tests/test_dependency_rules.py` enforces coupling
  caps per adapter.
- **A definition must behave the same on every runtime.** A new behaviour needs
  a case in `tests/test_runtime_conformance.py` and a named guarantee in
  `micro_agent/runtime/guarantees.py`.
- **Fail closed.** Unsupported configuration is rejected at startup, not
  ignored. A bare model reference is rejected; `provider: fake` must be explicit.
- **Definitions hold references, never secret values** (`credential_ref`).
- **Do not hide or waive failures.** `docs/IMPLEMENTATION_STATUS.md` records
  known failures; only claim what you ran. Report test counts you measured.
- Keep docs, `CHANGELOG.md` (completed work) and `TODO.md` (open work) in step
  with behaviour changes. Significant design changes need an ADR.
- Never commit secrets, `.env` files or generated build output (`site/`, `dist/`).
