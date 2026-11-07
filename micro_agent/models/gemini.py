"""Google Gemini (Generative Language API) model provider.

The adapter translates the runtime-neutral conversation and tool contracts to
Gemini's native ``generateContent`` wire format over the project's existing
HTTP client, so the custom runtime can use Gemini without importing the Google
ADK. API keys are sent in the ``x-goog-api-key`` header, never in the URL, so
they cannot leak through access logs or exception messages. Vertex AI
(OAuth/ADC) is out of scope; inject an ``http_client`` that signs requests if
it is required.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from micro_agent.models.model import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ModelStreamEvent,
    ProviderCapabilities,
)
from micro_agent.observability import Telemetry

DEFAULT_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com"

# Generation keys accepted in either snake_case or the wire's camelCase form.
_GENERATION_ALIASES = {
    "max_tokens": "maxOutputTokens",
    "max_output_tokens": "maxOutputTokens",
    "top_p": "topP",
    "top_k": "topK",
    "stop": "stopSequences",
    "stop_sequences": "stopSequences",
    "candidate_count": "candidateCount",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
    "response_mime_type": "responseMimeType",
}

# Gemini accepts an OpenAPI-3 subset of JSON Schema and rejects the rest.
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "anyOf",
        "title",
    }
)

_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
}


@dataclass
class GeminiConfig:
    """Connection settings for the Gemini Generative Language API."""

    endpoint: str = DEFAULT_GEMINI_ENDPOINT
    model_id: str = ""
    api_key: str | None = None
    api_version: str = "v1beta"
    timeout_seconds: float = 30.0
    default_headers: dict[str, str] = field(default_factory=dict)
    trust_env: bool = False
    verify_tls: bool = True
    proxy: str | None = None
    http_client: httpx.AsyncClient | None = None
    telemetry: Telemetry | None = None


class GeminiProvider(ModelProvider):
    """Model provider backed by Gemini's native ``generateContent`` API."""

    def __init__(self, config: GeminiConfig) -> None:
        if not config.model_id:
            raise ValueError("Gemini model_id is required")
        if config.timeout_seconds <= 0:
            raise ValueError("Gemini timeout must be greater than zero")
        self._config = config
        base = config.endpoint.rstrip("/")
        version = config.api_version.strip("/")
        self._base = base if base.endswith(f"/{version}") else f"{base}/{version}"
        self._owns_client = config.http_client is None
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **config.default_headers,
        }
        if config.api_key:
            self._headers["x-goog-api-key"] = config.api_key
        self._client = config.http_client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            trust_env=config.trust_env,
            verify=config.verify_tls,
            proxy=config.proxy,
        )

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self._headers)
        if self._config.telemetry is not None:
            self._config.telemetry.inject_context(headers)
        return headers

    def _model_path(self, config: ModelConfig) -> str:
        model = (config.model_id or self._config.model_id).strip("/")
        return model if model.startswith("models/") else f"models/{model}"

    def _payload(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                text = _content_text(content)
                if text:
                    system.append(text)
            elif role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                name = call_names.get(call_id) or str(message.get("name") or call_id or "tool")
                _append_parts(
                    contents,
                    "user",
                    [
                        {
                            "functionResponse": {
                                "name": name,
                                "response": _tool_response(content),
                            }
                        }
                    ],
                )
            elif role == "assistant":
                parts: list[dict[str, Any]] = []
                text = _content_text(content)
                if text:
                    parts.append({"text": text})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    if call.get("id"):
                        call_names[str(call["id"])] = name
                    parts.append(
                        {"functionCall": {"name": name, "args": _tool_arguments(function)}}
                    )
                if parts:
                    _append_parts(contents, "model", parts)
            else:
                text = _content_text(content)
                if text:
                    _append_parts(contents, "user", [{"text": text}])

        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        declarations = [_function_declaration(tool) for tool in tools or []]
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        generation = _generation_config(config.generation)
        if generation:
            payload["generationConfig"] = generation
        return payload

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate a response through ``models/{model}:generateContent``."""
        response = await self._client.post(
            f"{self._base}/{self._model_path(config)}:generateContent",
            json=self._payload(config, messages, tools),
            headers=self._request_headers(),
        )
        response.raise_for_status()
        return _parse_response(response.json())

    async def stream(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream text deltas from ``:streamGenerateContent?alt=sse``."""
        text_parts: list[str] = []
        tool_requests: list[dict[str, Any]] = []
        finish_reason = "stop"
        usage: dict[str, int] = {}
        async with self._client.stream(
            "POST",
            f"{self._base}/{self._model_path(config)}:streamGenerateContent?alt=sse",
            json=self._payload(config, messages, tools),
            headers=self._request_headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                chunk = _parse_response(json.loads(raw), id_offset=len(tool_requests))
                if chunk.content:
                    text_parts.append(chunk.content)
                    yield ModelStreamEvent(delta=chunk.content)
                tool_requests.extend(chunk.tool_requests)
                if chunk.usage:
                    usage = chunk.usage
                if chunk.finish_reason != "stop" or chunk.metadata.get("finished"):
                    finish_reason = chunk.finish_reason
        yield ModelStreamEvent(
            response=ModelResponse(
                content="".join(text_parts),
                tool_requests=tool_requests,
                finish_reason=finish_reason,
                usage=usage,
            )
        )

    def capabilities(self) -> ProviderCapabilities:
        """Report native features supported by this adapter."""
        return ProviderCapabilities(tool_use=True, streaming=True, structured_output=False)

    async def health_check(self) -> bool:
        """Probe the model-list resource."""
        try:
            response = await self._client.get(
                f"{self._base}/models",
                params={"pageSize": 1},
                headers=self._request_headers(),
            )
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        """Release the underlying HTTP client when the provider owns it."""
        if self._owns_client:
            await self._client.aclose()


def _append_parts(contents: list[dict[str, Any]], role: str, parts: list[dict[str, Any]]) -> None:
    """Append parts, merging consecutive same-role turns as Gemini requires."""
    if contents and contents[-1]["role"] == role:
        contents[-1]["parts"].extend(parts)
    else:
        contents.append({"role": role, "parts": list(parts)})


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(
            item["text"] if isinstance(item, dict) and isinstance(item.get("text"), str) else ""
            for item in content
        )
    return str(content)


def _tool_arguments(function: dict[str, Any]) -> dict[str, Any]:
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _tool_response(content: Any) -> dict[str, Any]:
    """Gemini requires ``functionResponse.response`` to be a JSON object."""
    text = _content_text(content)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"result": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _function_declaration(tool: dict[str, Any]) -> dict[str, Any]:
    declaration: dict[str, Any] = {
        "name": tool["name"],
        "description": tool.get("description", ""),
    }
    schema = _sanitize_schema(tool.get("input_schema") or {})
    # An object schema without properties is rejected by the API; omit it.
    if isinstance(schema, dict) and schema.get("properties"):
        declaration["parameters"] = schema
    return declaration


def _sanitize_schema(schema: Any) -> Any:
    """Reduce JSON Schema to the subset Gemini accepts."""
    if isinstance(schema, list):
        return [_sanitize_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: _sanitize_schema(sub) for name, sub in value.items()}
        elif key in {"items", "anyOf"}:
            cleaned[key] = _sanitize_schema(value)
        elif key == "type" and isinstance(value, list):
            non_null = [item for item in value if item != "null"]
            cleaned["type"] = non_null[0] if non_null else "string"
            if len(non_null) != len(value):
                cleaned["nullable"] = True
        else:
            cleaned[key] = value
    return cleaned


def _generation_config(generation: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for key, value in generation.items():
        config[_GENERATION_ALIASES.get(key, key)] = value
    stop = config.get("stopSequences")
    if isinstance(stop, str):
        config["stopSequences"] = [stop]
    return config


def _parse_usage(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage: dict[str, int] = {}
    mapping = {
        "promptTokenCount": "prompt_tokens",
        "candidatesTokenCount": "completion_tokens",
        "totalTokenCount": "total_tokens",
    }
    for source, target in mapping.items():
        if type(raw.get(source)) is int:
            usage[target] = raw[source]
    if "total_tokens" not in usage and usage:
        usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    return usage


def _parse_response(data: Any, *, id_offset: int = 0) -> ModelResponse:
    if not isinstance(data, dict):
        raise ValueError("Gemini response must be a JSON object")
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    text_parts: list[str] = []
    tool_requests: list[dict[str, Any]] = []
    for part in (candidate.get("content") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            continue  # reasoning summaries are not part of the answer
        if isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        call = part.get("functionCall")
        if isinstance(call, dict):
            args = call.get("args")
            tool_requests.append(
                {
                    "id": str(call.get("id") or f"gemini-call-{id_offset + len(tool_requests)}"),
                    "name": str(call.get("name") or ""),
                    "arguments": args if isinstance(args, dict) else {},
                }
            )
    raw_reason = candidate.get("finishReason")
    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
    if block_reason and not candidate:
        finish_reason = "content_filter"
    elif raw_reason:
        finish_reason = _FINISH_REASONS.get(str(raw_reason), str(raw_reason).lower())
    else:
        finish_reason = "stop"
    return ModelResponse(
        content="".join(text_parts),
        tool_requests=tool_requests,
        finish_reason=finish_reason,
        usage=_parse_usage(data.get("usageMetadata")),
        metadata={
            "model": data.get("modelVersion"),
            "finished": bool(raw_reason or block_reason),
            **({"block_reason": block_reason} if block_reason else {}),
        },
    )


__all__ = ["DEFAULT_GEMINI_ENDPOINT", "GeminiConfig", "GeminiProvider"]
