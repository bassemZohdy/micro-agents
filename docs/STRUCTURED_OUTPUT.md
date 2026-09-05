# Structured Output

The built-in runtime exposes `structured_output` only when the selected model provider advertises provider-native support.

For OpenAI-compatible chat-completions providers, a non-empty Micro-Agent output contract is translated to a strict JSON Schema and sent through the provider `response_format` field. Explicit `generation.response_format` configuration is preserved and takes precedence.

Unsupported providers and native Google ADK model selection continue to report
`structured_output: false`; no capability is inferred from an SDK feature that
is not wired through the runtime-neutral contract. When an injected provider
advertises structured output, the Google ADK adapter forwards the same strict
JSON Schema generation settings used by the custom runtime.

Streaming and checkpointing remain separate capabilities and are not implied by structured-output support.
