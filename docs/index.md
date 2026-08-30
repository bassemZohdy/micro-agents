# Micro-Agents

**Micro-Agents** is an open architecture and reference framework for building
cloud-native, independently deployable AI agents.

A runnable vertical slice exists today: a YAML definition loads into a
`DefaultMicroAgent` bound to the ADK runtime (fake model for CI, OpenAI-compatible
endpoint in production) and is served over FastAPI with health, capability, and
A2A agent-card endpoints.

See the [architecture](architecture/MICRO_AGENT_ARCHITECTURE.md) documents and
the repository [README](https://github.com/bassem/micro-agents) for the full
picture; open work is tracked in [TODO.md](https://github.com/bassem/micro-agents/blob/main/TODO.md).
