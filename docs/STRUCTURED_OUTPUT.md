# Structured Output

The built-in runtime exposes `structured_output` only when the selected model provider advertises provider-native support.

For OpenAI-compatible chat-completions providers, a non-empty Micro-Agent output contract is translated to a strict JSON Schema and sent through the provider `response_format` field. Explicit `generation.response_format` configuration is preserved and takes precedence.

Unsupported providers and the Google ADK runtime continue to report `structured_output: false`; no capability is inferred from an SDK feature that is not wired through the runtime-neutral contract.

Streaming and checkpointing remain separate capabilities and are not implied by structured-output support.
