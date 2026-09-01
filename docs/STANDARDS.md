# Standards Baseline

Baseline date: 2026-09-01.

The project targets the latest released stable protocol and does not silently
adopt drafts or release candidates.

## A2A

Target: **A2A v1.0.1**.

Official references:

- [A2A releases](https://github.com/a2aproject/A2A/releases)
- [A2A v1 changes](https://a2a-protocol.org/latest/whats-new-v1/)
- [Agent discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [Official Python SDK](https://github.com/a2aproject/a2a-python)

Relevant v1 requirements for this project:

- public card path: `/.well-known/agent-card.json`
- endpoint/protocol declarations in ordered `supportedInterfaces`
- protocol binding and protocol version per interface
- standard security schemes and requirements
- complete AgentSkill metadata
- at least one real standard message/task binding

Current status: **tested subset implemented**. The official `a2a-sdk` serves
`/.well-known/agent-card.json` and mounts JSON-RPC `message/send` when enabled;
the integration suite resolves the card with the official client and completes
a non-streaming task. Streaming, push notifications, durable task state, and
in-flight cancellation remain open, so this is not a claim of full production
conformance.

## MCP

Target stable specification: **2025-11-25**.

Official references:

- [MCP 2025-11-25 specification](https://modelcontextprotocol.io/specification/2025-11-25)
- [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Official Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [July 2026 release candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)

The July 2026 document is a release candidate at this baseline date. Its
stateless core, extensions, tasks, apps, and authorization changes may be
evaluated behind an experimental flag after the stable implementation works,
but they do not define the stable contract yet.

Stable transport expectations:

- stdio for local child-process servers
- Streamable HTTP for remote servers
- legacy SSE compatibility is documented separately

Current status: **tested subset implemented**. The official MCP Python SDK is
wired behind the `McpClient` SPI for stdio and Streamable HTTP (with legacy SSE
compatibility), including initialization negotiation, discovery, bounded
calls, security controls, graceful close, bounded automatic reconnect, and
real FastMCP interop tests. Notifications are consumed but not surfaced, and
remote production load testing remains open.

## Google ADK

Google ADK is a runtime implementation choice, not an interoperability
protocol. `runtimes/adk` is the custom built-in loop; the optional
`runtimes/google_adk` package depends on and invokes supported Google ADK APIs
and is covered by adapter tests. The adapter is still pre-release and does not
claim full production service integration.

## Version policy

- Record the protocol release in dependency/configuration metadata.
- Test the oldest and newest supported compatible versions where practical.
- Reject unsupported major versions.
- Treat draft support as opt-in and label it experimental.
- Update this file, compatibility tests, schema, and changelog together.
