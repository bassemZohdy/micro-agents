# Micro-Agents — TODO

Remaining implementation backlog. See CHANGELOG.md for completed milestones.

Review findings B–P from the 2026-08-30 audit (commit `fe4d8e6`) have been
implemented and verified in this pass: 299 tests pass (246 unit + 53
integration/e2e), `ruff` and strict `mypy micro_agent runtimes` are clean, and
the JSON Schema drift check passes. The findings sections below are kept as a
record with evidence; the open work is the Micro-Agent Cloud workstream
(gated until the standalone framework is production-hardened, including a
real MCP wire client and OpenTelemetry export — see ADRs 0002/0003).

---

# Remaining Review Findings

## B. ADK runtime is a stub (Milestone 14)

- [x] Wire real model provider (not just FakeModelProvider)
      → `OpenAICompatProvider` (micro_agent/models/openai_compat.py) talks to any
      /chat/completions endpoint; selected via `AdkRuntimeConfig.model_provider`.
      FakeModelProvider remains the CI default.
- [x] Implement proper start/stop lifecycle — `start()` now health-checks the
      model provider before marking the agent started; `stop()` logs state;
      `close()` releases HTTP connection pools.
- [x] Wire sessions, memory, skills into invoke path
      → session history replayed/persisted via SessionProvider (TTL from
      definition); memory auto-stored per MemoryPolicy.auto_store; skills
      rendered into the system prompt as semantic capabilities.
- [x] Generalize tool resolution — definition tools resolve against a built-in
      registry (`_BUILTIN_TOOLS`); unknown names are reported as
      `unresolved_tools` instead of being silently dropped.

## C. MCP is interface-only (Milestone 9)

- [x] Implement fake MCP client for testing
      → `FakeMcpClient` (micro_agent/mcp/client.py) with configurable tools,
      resources, prompts, handlers, and failure injection.
- [x] Wire MCP tools into runtime invoke path
      → `McpConnectionManager` connects servers declared in the definition at
      `start()`, exposes discovered tools as `server:tool` Tool adapters,
      preserves resources/prompts metadata, and adds an MCP health probe;
      `AdkRuntime.close()` disconnects gracefully.
- [x] Add MCP security (TLS, credential redaction, endpoint validation)
      → `McpSecurityPolicy`: TLS enforcement (localhost exception), endpoint
      allowlist, transport validation, response size limits; credentials are
      resolved outside config reprs and redacted by the structured logger.
- [x] Demonstrate MCP-by-configuration acceptance
      → tests/test_mcp_integration.py attaches MCP servers purely via the
      definition + client factory and exercises a full invoke.

## D. A2A is dataclass-only (Milestone 21)

- [x] Implement AgentCard generation from definition
      → `agent_card_from_definition()` builds a card (name, version, url from
      the definition's A2A endpoint or a base URL, capabilities, security,
      protocol metadata).
- [x] Add skills-mapping logic
      → `skills_mapping()` converts definition `SkillDefinition`s to A2A
      `AgentSkill`s (also serves as the N conversion helper).
- [x] Create agent-card endpoint
      → `GET /.well-known/agent.json` served by `create_app()`.
- [x] Test with compatible independent client
      → tests/test_a2a_integration.py fetches the card as raw JSON with a
      plain HTTP client and validates the A2A card contract without importing
      framework types.

## E. Observability not wired (Milestone 17)

- [x] Wire StructuredLogger/MetricsCollector into invocation path
      → `Telemetry` facade (logger + metrics + span recorder) passed into
      AdkRuntime and the HTTP layer; invoke/logs/metrics all flow through it.
- [x] Record invocation count, errors, model latency, tokens, tool calls via
      MetricsCollector
      → agent_invocations_total, agent_invocation_errors_total,
      agent_invocation_latency_ms, model_latency_ms, model_tokens_total,
      tool_calls_total, tool_latency_ms.
- [x] Add secret redaction to StructuredLogger
      → sensitive key patterns redacted recursively; `register_secret()`
      redacts known secret values.
- [x] Demonstrate end-to-end trace through model/tool/MCP
      → agent/model/tool span hierarchy with shared trace_id +
      parent_span_id, including MCP tool adapters (tests/test_runtime_behavior.py,
      tests/test_mcp_integration.py).

## F. Health checks are passive (Milestone 16)

- [x] Add active dependency probes (model, MCP, session, memory)
      → `HealthChecker.add_dependency(..., probe=...)`; `probe_readiness()`
      executes probes and updates statuses; AdkRuntime exposes `health_probes()`
      (model/session/memory) and `__main__` registers them. MCP probe lands
      with finding C.
- [x] Make check_liveness() check actual process health
      → optional liveness probe callable + `set_alive()`; /health/live reflects it.
- [x] Allow dependency status updates after registration
      → `update_status(name, status, details)`.

## G. Session / Memory / Knowledge gaps

- [x] Implement session lifecycle (created_at, expires_at, expiration check)
      → `InMemorySessionProvider` tracks timestamps, supports per-provider and
      per-call TTL with sliding refresh, drops expired sessions on get/list.
- [x] Enforce MemoryPolicy (max_entries, ttl_seconds)
      → `InMemoryMemoryProvider` accepts a policy; max_entries evicts
      least-recently-stored, ttl expires on read, auto_store exposed. Also
      fixed scope default mismatch that made stored entries unfetchable.
- [x] Implement concrete KnowledgeRetriever
      → `InMemoryKnowledgeRetriever` with relevance-ranked keyword retrieval
      and sha256 `content_hash` + version integrity metadata.

## H. Policy not integrated (Milestones 18–20)

- [x] Wire PolicyEvaluator into runtime invocation path
      → `AdkRuntimeConfig.policy` builds a PolicyEvaluator; tool calls are
      checked (tool + side-effect policy) before execution, denied MCP servers
      fail startup, denials are logged and counted (`policy_denials_total`).
- [x] Load security.policy_refs/credential_refs from definition
      → `build_security_context()` (micro_agent/security/context.py) populates
      a SecurityContext attached to every RuntimeAgent; credential values are
      resolved on demand, never stored.
- [x] Apply OperationRegistry to side-effect execution
      → `AdkRuntimeConfig.operation_registry`; tool calls with an
      `idempotency_key` argument are deduplicated (`was_deduplicated` in the
      tool result).
- [x] Move identity/policy/side_effects out of observability module
      → now `micro_agent/security/` (identity, policy, side_effects, context)
      and `micro_agent/health/`; observability keeps only telemetry plus
      backward-compat re-exports.

## I. Timeouts not enforced (Milestones 7–8)

- [x] Enforce ToolMetadata.timeout_seconds
      → `asyncio.wait_for` around every tool execution (default 30s when unset).
- [x] Enforce ModelConfig.timeout_seconds
      → `asyncio.wait_for` around model generate calls.
- [x] Add tool invocation tracing and MetricsCollector integration
      → tool spans (tool.<name>) + tool_calls_total/tool_latency_ms metrics;
      latency also remains in response metadata.

## J. RuntimeSemantics not honored

- [x] Wire timeout_seconds, max_iterations, error_policy into runtime
      → invoke wraps the whole run in `timeout_seconds` (wait_for); a real
      agent loop iterates model→tool up to `max_iterations` (excess reported
      via metadata); `error_policy` fail/retry/fallback enforced outside the
      prompt (fail raises, retry re-runs once, fallback returns an error
      response).

## M. CI/CD gaps

All automation is in place (`.github/workflows/ci.yml`, `release.yml`); the
remote jobs activate on the next push.

- [x] Add integration tests to CI → dedicated job, `pytest -m integration`.
- [x] Add E2E tests to CI → dedicated job, `pytest -m e2e` (real-socket
      service test).
- [x] Add container build + smoke test → `container` job builds the image and
      curls /health/live, /health/ready, /v1/capabilities against the running
      container with the residency example mounted.
- [x] Add SBOM generation → `sbom` job (anchore/sbom-action, SPDX) + SBOM in
      the release workflow.
- [x] Add release versioning/tagging → `release.yml` on `v*` tags.
- [x] Add container image publishing → GHCR via docker/metadata + build-push.
- [x] Add release-notes automation → GitHub release with generated notes.
- [x] Add documentation publishing → mkdocs.yml + `docs` gh-deploy job.
- [x] Type-check runtimes/ in CI → mypy exclusion removed, strict clean for
      `micro_agent runtimes` (51 files).
- [x] Add ADRs for key decisions → docs/adr/0001–0006.
- [x] Add docs site (mkdocs/sphinx) → mkdocs.yml with architecture, schema,
      and ADR navigation.

## N. Duplicate types

- [x] Consolidate SkillDefinition (pydantic vs dataclass)
      → the definition pydantic model is canonical; `micro_agent.skills`
      re-exports it.
- [x] Consolidate A2AConfig (definition vs interoperability)
      → the definition pydantic model is canonical; `interoperability.a2a`
      re-exports it.
- [x] Add conversion helpers for skill shapes
      → `skills_mapping()` (definition → A2A AgentSkill) and
      `capability_contract_from_definition()` (definition → CapabilityContract).

## O. Test depth

- [x] Add behavioral acceptance tests (agent lifecycle and ADK invoke paths are
      now covered; MCP/session/memory/A2A/health behaviour is not)
      → behavioral suites: tests/test_runtime_behavior.py, test_state_providers,
      test_mcp_integration, test_policy_integration, test_a2a_integration.
- [x] Test real network service, MCP-by-configuration, multi-replica session
      → real uvicorn socket test + MCP-by-configuration invoke
      (tests/test_mcp_integration.py) + multi-replica shared SQLite sessions
      (tests/test_acceptance.py, SqliteSessionProvider persistent reference).

## L. Deployment gap (residual from Milestones 22–23)

- [x] `deploy/kubernetes/deployment.yaml` mounts ConfigMap
      `micro-agent-definition` (holding `agent.yaml`), but no such ConfigMap is
      defined — only `micro-agent-config` (log-level). The pod cannot schedule.
      Add a definition ConfigMap or document how to supply one.
      → Added `deploy/kubernetes/definition-configmap.yaml` (key `agent.yaml`
      with the residency-renewal example).

## P. Working-tree hygiene

- [x] `.commandcode/settings.json` still contains a malformed permission-allow
      entry that is an entire flattened `cat > TODO.md << 'ENDOFFILE' … ENDOFFILE`
      heredoc (line 14), plus a compound `echo`/`grep`/`pytest` entry. Neither can
      ever match a future command. Remove both.

---

# Micro-Agent Cloud — Future Workstream

Do not implement until standalone framework is production-capable.

## C0 — Architecture
- [ ] Define responsibilities and boundaries
- [ ] Document service-discovery vs agent-discovery
- [ ] Define common abstractions and extension model

## C1 — Agent Registry
- [ ] Define agent descriptor and semantic discovery
- [ ] Build minimal local registry

## C2 — Discovery
- [ ] Agent/capability/skill discovery client
- [ ] Health-aware selection

## C3 — Distributed Configuration
- [ ] Central definition storage with environment overlays

## C4 — Resilience
- [ ] Retry, circuit breaker, bulkhead, rate limiting

## C5 — Gateway
- [ ] A2A routing, auth, rate limits

## C6 — Security and Policy
- [ ] Agent identity, skill authorization, audit

## C7 — Distributed Observability
- [ ] Cross-agent tracing, cost aggregation, topology views

---

# Deferred

- [ ] LangChain runtime
- [ ] Visual designer
- [ ] Workflow engine
- [ ] Agent marketplace
- [ ] Distributed memory platform
