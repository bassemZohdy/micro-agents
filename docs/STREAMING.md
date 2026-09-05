# Streaming

Streaming is capability-gated end to end. A client requests it with `Accept: text/event-stream` on `POST /v1/invoke`. If the selected runtime/provider does not implement streaming, the API returns HTTP 406 before invocation.

When supported, the response is Server-Sent Events:

- `delta` — provider content increment (`{"delta":"..."}`).
- `final` — the complete normal invocation response, including request/session ids, metadata, tool results, and status.
- `error` — a stable redacted streaming error if execution fails after the HTTP stream starts.

The OpenAI-compatible adapter uses the provider's chat-completions SSE protocol and reconstructs streamed tool calls before continuing the runtime tool loop. The built-in fake provider exposes streaming only when `stream_chunks` is explicitly configured. The Google ADK adapter exposes streaming when an injected model provider advertises it, translating provider deltas through ADK's SSE execution mode; native ADK model selection remains conservative until its provider capability is known.

Once any output delta has been delivered, invocation-level retry/fallback is suppressed because replay could duplicate already-visible output.
