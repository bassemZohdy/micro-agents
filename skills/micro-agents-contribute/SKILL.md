---
name: micro-agents-contribute
description: Change the Micro-Agents repository correctly, covering environment setup, the CI gates (ruff, strict mypy, schema drift, pytest markers, mkdocs strict, release and capability checks), where code lives, dependency rules, and the docs, changelog and ADR expectations. Use when fixing a bug, adding a feature or test, updating the definition schema, or preparing a pull request in this repo.
license: Apache-2.0
compatibility: Needs Python 3.11 or 3.12 and a source checkout of micro-agents. Docker, Redis and PostgreSQL are only needed for the integration suites.
metadata:
  project: micro-agents
---

# Contribute to Micro-Agents

## Set up

```bash
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev,docs]"                # add: adk, langgraph, redis, postgres, otel, mcp, auth, a2a
git switch -c my-change main                          # never commit to main; it is protected
```

## Know where things live

| Area | Path |
|---|---|
| Definition models, loader, schema generator | `micro_agent/definition/` |
| Bootstrap, env config, runtime selection | `micro_agent/config/` |
| Cross-runtime behavior (tools, model, invocation, approvals, checkpoints) | `micro_agent/governance/` |
| HTTP API, A2A, MCP | `micro_agent/interoperability/`, `micro_agent/mcp/` |
| State providers (session, memory, idempotency) | `micro_agent/session/`, `micro_agent/memory/`, `micro_agent/state.py` |
| Runtime adapters | `runtimes/custom`, `runtimes/google_adk`, `runtimes/langgraph` |
| Optional control plane | `cloud/` |
| Docs, ADRs, generated schemas | `docs/`, `docs/adr/`, `docs/schemas/` |

## The gates (run these before opening a PR)

```bash
ruff check . && ruff format --check .
mypy micro_agent cloud runtimes                        # strict
pytest -m "not integration and not e2e and not otel"   # default selection, 80% coverage gate
python -m micro_agent.definition.schema && git diff --exit-code docs/schemas/   # schema drift
python tools/validate_release.py                       # version/image/compat alignment
python tools/runtime_capabilities.py --check           # generated table in docs/RUNTIMES.md
python tools/validate_skills.py                        # skills and plugin manifests
mkdocs build --strict
```

Targeted suites by marker: `integration`, `e2e`, `adk`, `langgraph`, `otel`,
`postgres` (needs `MICRO_AGENT_TEST_PG_DSN`). Adapter tests skip cleanly when
their extra is absent; CI runs them in dedicated jobs, and `runtime-parity`
installs `adk` and `langgraph` together with Redis 7 and PostgreSQL 16 services.

## Rules

- **Behavior must be runtime-neutral.** A new behavior goes into the governance
  layer and gets a case in `tests/test_runtime_conformance.py` (and a guarantee
  in `micro_agent/runtime/guarantees.py` if it is a new uniform guarantee). Do
  not add a per-runtime special case.
- **Layering** is enforced by `tests/test_dependency_rules.py`: only
  `micro_agent/config/runtimes.py` imports `runtimes.*`, adapters never import
  each other, optional frameworks are imported behind guards, adapter fan-out has
  a cap.
- **Fail closed**: unsupported configuration is rejected at startup, never
  ignored or silently downgraded.
- **Changing the definition model** requires regenerating `docs/schemas/`, a
  compatibility fixture/test update (`tests/fixtures/compatibility/`), and the
  configuration docs together.
- **No secrets** in code, definitions, fixtures or logs. Definitions hold
  `credential_ref` names only.
- **Report only what you ran.** Do not hide, skip or waive a failure; if a gate
  fails for an unrelated reason, say so. Record real numbers (test counts,
  coverage) from your run, not estimates.
- Write tests with the change; prefer deterministic fakes (`FakeModelProvider`)
  over network calls.

## Pull request

1. Focused change, tests, docs updated where behavior changed.
2. `CHANGELOG.md` `[Unreleased]` for completed work; `TODO.md` only for open work.
3. A significant design decision needs an ADR in `docs/adr/` that separates the
   accepted design from its implementation status; add it to `mkdocs.yml`.
4. Commit style: `type(scope): short description` with type one of `feat`, `fix`,
   `docs`, `test`, `refactor`, `chore`, `ci`.
5. Push the branch and open a PR to `main`. The ruleset requires the CI jobs
   (`lint`, `typecheck`, `test (3.11)`, `test (3.12)`, `schema`, `docs`, `adk`,
   `langgraph`, `runtime-parity`, `integration`, `e2e`, `otel`, `container`,
   `build`, `manifests`, `security-*`); do not merge until they are green.

## Releases (maintainers)

The version lives in `pyproject.toml`, the Kubernetes image pin and (for skills
and plugins) every manifest checked by `tools/validate_skills.py`. Move
`[Unreleased]` into a versioned changelog section, bump all of them together, and
tag `vX.Y.Z`. Tags are immutable by ruleset: tag only from `main` after the
release PR is merged.

## References

- Contributing: https://github.com/bassemZohdy/micro-agents/blob/main/CONTRIBUTING.md
- Implementation status: https://bassemzohdy.github.io/micro-agents/IMPLEMENTATION_STATUS/
- Standards and compatibility: https://bassemzohdy.github.io/micro-agents/COMPATIBILITY/
