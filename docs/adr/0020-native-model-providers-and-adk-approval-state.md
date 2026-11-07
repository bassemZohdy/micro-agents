# ADR 0020: Native Gemini and Azure OpenAI providers; ADK approval state stays in the session

Date: 2026-11-07 · Status: Accepted

## Context

Two items were recorded as open framework gaps:

1. The built-in model providers covered fake, OpenAI-compatible and Anthropic.
   Native Google Gemini and Azure OpenAI were listed as optional future adapters.
   Selecting `provider: gemini` on the custom runtime failed at bootstrap, because
   Google providers were accepted only as the Google ADK runtime's native model.
2. The Google ADK adapter did not use the durable `ApprovalStore` that the custom
   runtime reads from `MICRO_AGENT_APPROVAL_ENDPOINT`.

## Decision

**Providers.** Add `GeminiProvider` and `AzureOpenAIProvider` to
`micro_agent.models`, built on the existing `httpx` client with no new runtime
dependency.

- Gemini uses the native `generateContent` wire format. The API key travels in
  the `x-goog-api-key` header, never in the URL, so it cannot leak through access
  logs or exception messages. Tool JSON Schemas are reduced to the OpenAPI subset
  Gemini accepts. Structured output is reported as unsupported, as for Anthropic,
  so an output contract cannot be silently downgraded.
- Azure OpenAI subclasses the OpenAI-compatible provider through two small URL
  hooks, so request/response translation, tool calling and streaming are not
  duplicated. Only the deployment URL, `api-version` query parameter and
  `api-key` header differ.
- Bootstrap precedence is unchanged where it was already defined: with the Google
  ADK runtime and no endpoint, `google`/`gemini` still selects ADK's native
  model. In every other case `google`/`gemini` now builds `GeminiProvider`.
- Vertex AI (OAuth/ADC) and Microsoft Entra ID are out of scope. Operators supply
  an `http_client` that signs requests, which every provider already accepts.

**ADK approvals.** Do not wire `ApprovalStore` into the ADK adapter. Its
approval continuations are ADK `ToolConfirmation` events stored in the ADK
session transcript, which the Redis, PostgreSQL and SQLite session bridges already
make durable and shared. A second store would hold the same state in two places
that can diverge. Bootstrap already rejects `MICRO_AGENT_APPROVAL_ENDPOINT`
combined with the ADK runtime, so the setting cannot be silently ignored.

## Consequences

- Two more first-party providers need contract tests, but add no dependencies.
- Azure's API version is pinned by default. Changing it from configuration alone
  is not supported; it is a programmatic `AzureOpenAIConfig.api_version`.
- Durable approvals under the ADK runtime depend on an external session provider,
  not on `MICRO_AGENT_APPROVAL_ENDPOINT`. The deployment documentation states this.
