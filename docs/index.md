# Micro-Agents

Micro-Agents defines an architecture and provides a pre-release Python
reference framework for narrowly scoped, independently deployable AI agents.

The repository currently includes a strict definition schema, runtime-neutral
interfaces, a deterministic custom model/tool loop, provider seams, an HTTP
service, container assets, and an extensive automated test suite. It is an
architecture prototype and contract testbed, not yet a production-ready Google
ADK, A2A, or MCP implementation.

## Start here

- [Getting Started](GETTING_STARTED.md) — install, validate, and run the current
  fake-provider development slice
- [Implementation Status](IMPLEMENTATION_STATUS.md) — audited capability and
  gap matrix at the latest reviewed commit
- [Configuration](CONFIGURATION.md) — definition fields, environment values,
  precedence, and current bootstrap limitations
- [HTTP API](API.md) — implemented routes and their current semantics
- [Deployment](DEPLOYMENT.md) — development container/Kubernetes assets and the
  production hardening checklist
- [Standards](STANDARDS.md) — A2A, MCP, and Google ADK compatibility baselines

The normative model lives in the [Micro-Agent
Architecture](architecture/MICRO_AGENT_ARCHITECTURE.md) and [Twelve-Factor
Micro-Agent](architecture/TWELVE_FACTOR_MICRO_AGENT.md) documents. Open work is
prioritized in [TODO.md](https://github.com/bassemZohdy/micro-agents/blob/main/TODO.md),
and completed work is recorded in the
[changelog](https://github.com/bassemZohdy/micro-agents/blob/main/CHANGELOG.md).
