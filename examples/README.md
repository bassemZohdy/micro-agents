# Examples

| File | Status | Notes |
|---|---|---|
| `notification-agent.yaml` | Executable | Boots with the deterministic fake provider; used by the CI container smoke test. |
| `support-desk-agent.yaml` | Executable | Boots as written: fake provider, in-memory session and memory, built-in `echo` tool. |
| `residency-renewal.yaml` | Conceptual | Demonstrates the full schema (native tools beyond the built-ins, MCP servers, external state, policies) that require programmatic or real integrations. Do not boot as-is. |

Labeling convention: every example starts with a first-line comment stating
`Executable` or a conceptual notice. Executability is enforced by
`tests/test_examples.py` — any example marked executable must load, build a
runtime from configuration alone, and serve an invocation.
