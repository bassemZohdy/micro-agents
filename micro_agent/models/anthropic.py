"""Anthropic Messages API model provider.

The adapter translates the runtime-neutral conversation and tool contracts to
Anthropic's native Messages wire format. It intentionally keeps the provider
dependency-free beyond the project's existing HTTP client so deployments can
select it through configuration without importing an SDK-specific runtime.
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


@dataclass
class AnthropicConfig:
    """Connection settings for the Anthropic Messages API."""

    endpoint: str = "https://api.anthropic.com"
    model_id: str = ""
    api_key: str | None = None
    api_version: str = "2023-06-01"
    timeout_seconds: float = 30.0
    default_headers: dict[str, str] = field(default_factory=dict)
    trust_env: bool = False
    verify_tls: bool = True
    proxy: str | None = None
    http_client: httpx.AsyncClient | None = None
    telemetry: Telemetry | None = None


class AnthropicProvider(ModelProvider):
    """Model provider backed by Anthropic's native Messages API."""

    def __init__(self, config: AnthropicConfig) -> None:
        if not config.model_id:
            raise ValueError("Anthropic model_id is required")
        if config.timeout_seconds <= 0:
            raise ValueError("Anthropic timeout must be greater than zero")
        self._config = config
        self._base_endpoint = config.endpoint.rstrip("/")
        self._messages_endpoint = self._versioned_endpoint("messages")
        self._models_endpoint = self._versioned_endpoint("models")
        self._owns_client = config.http_client is None
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "anthropic-version": config.api_version,
            **config.default_headers,
        }
        if config.api_key:
            self._headers["x-api-key"] = config.api_key
        self._client = config.http_client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            trust_env=config.trust_env,
            verify=config.verify_tls,
            proxy=config.proxy,
        )

    def _versioned_endpoint(self, resource: str) -> str:
        suffix = f"/{resource}" if self._base_endpoint.endswith("/v1") else f"/v1/{resource}"
        return f"{self._base_endpoint}{suffix}"

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self._headers)
        if self._config.telemetry is not None:
            self._config.telemetry.inject_context(headers)
        return headers

    def _payload(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        system: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                text = _content_text(content)
                if text:
                    system.append(text)
                continue
            if role == "tool":
                tool_id = str(message.get("tool_call_id") or message.get("name") or "tool")
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": _content_text(content),
                }
                _append_user_block(converted, block)
                continue
            if role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                text = _content_text(content)
                if text:
                    blocks.append({"type": "text", "text": text})
                for call in message["tool_calls"]:
                    function = call.get("function") or {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id") or "tool-call"),
                            "name": str(function.get("name") or ""),
                            "input": _tool_arguments(function.get("arguments")),
                        }
                    )
                _append_message(converted, "assistant", blocks)
                continue
            _append_message(converted, role if role in {"user", "assistant"} else "user", content)

        generation = dict(config.generation)
        max_tokens = generation.pop("max_tokens", 1024)
        if not isinstance(max_tokens, int) or max_tokens < 1:
            raise ValueError("Anthropic max_tokens must be a positive integer")
        payload: dict[str, Any] = {
            "model": config.model_id or self._config.model_id,
            "max_tokens": max_tokens,
            "messages": converted,
        }
        if system:
            payload["system"] = "\n\n".join(system)
        if tools:
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema", {}),
                }
                for tool in tools
            ]
        payload.update(generation)
        return payload

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate a response through ``POST /v1/messages``."""
        response = await self._client.post(
            self._messages_endpoint,
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
        """Stream text and tool-use deltas from the Anthropic SSE contract."""
        payload = self._payload(config, messages, tools)
        payload["stream"] = True
        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        stop_reason = "stop"
        usage: dict[str, int] = {}
        async with self._client.stream(
            "POST",
            self._messages_endpoint,
            json=payload,
            headers=self._request_headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                event = json.loads(raw)
                event_type = event.get("type")
                if event_type == "message_start":
                    _merge_usage(usage, event.get("message", {}).get("usage"))
                elif event_type == "content_block_start":
                    block = event.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        index = int(event.get("index", 0))
                        tool_calls[index] = {
                            "id": str(block.get("id") or "tool-call"),
                            "name": str(block.get("name") or ""),
                            "arguments": "",
                        }
                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if isinstance(text, str) and text:
                            text_parts.append(text)
                            yield ModelStreamEvent(delta=text)
                    elif delta.get("type") == "input_json_delta":
                        index = int(event.get("index", 0))
                        state = tool_calls.setdefault(
                            index,
                            {"id": "tool-call", "name": "", "arguments": ""},
                        )
                        state["arguments"] += str(delta.get("partial_json") or "")
                elif event_type == "message_delta":
                    delta = event.get("delta") or {}
                    if delta.get("stop_reason"):
                        stop_reason = str(delta["stop_reason"])
                    _merge_usage(usage, event.get("usage"))
        requests = [
            {
                "id": state["id"],
                "name": state["name"],
                "arguments": _tool_arguments(state["arguments"]),
            }
            for _, state in sorted(tool_calls.items())
        ]
        yield ModelStreamEvent(
            response=ModelResponse(
                content="".join(text_parts),
                tool_requests=requests,
                finish_reason=stop_reason,
                usage=usage,
            )
        )

    def capabilities(self) -> ProviderCapabilities:
        """Report native Messages features supported by this adapter."""
        return ProviderCapabilities(tool_use=True, streaming=True, structured_output=False)

    async def health_check(self) -> bool:
        """Probe the versioned Anthropic models endpoint."""
        try:
            response = await self._client.get(
                self._models_endpoint,
                headers=self._request_headers(),
            )
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        """Release the underlying HTTP client when the provider owns it."""
        if self._owns_client:
            await self._client.aclose()


def _append_message(messages: list[dict[str, Any]], role: str, content: Any) -> None:
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"] = _merge_content(messages[-1]["content"], content)
    else:
        messages.append({"role": role, "content": content})


def _append_user_block(messages: list[dict[str, Any]], block: dict[str, Any]) -> None:
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = _merge_content(messages[-1]["content"], [block])
    else:
        messages.append({"role": "user", "content": [block]})


def _merge_content(existing: Any, incoming: Any) -> list[Any]:
    def blocks(value: Any) -> list[Any]:
        if isinstance(value, list):
            return list(value)
        return [{"type": "text", "text": _content_text(value)}]

    return blocks(existing) + blocks(incoming)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _merge_usage(target: dict[str, int], source: Any) -> None:
    if not isinstance(source, dict):
        return
    if isinstance(source.get("input_tokens"), int):
        target["prompt_tokens"] = source["input_tokens"]
    if isinstance(source.get("output_tokens"), int):
        target["completion_tokens"] = source["output_tokens"]
    if "prompt_tokens" in target or "completion_tokens" in target:
        target["total_tokens"] = target.get("prompt_tokens", 0) + target.get("completion_tokens", 0)


def _parse_response(data: Any) -> ModelResponse:
    if not isinstance(data, dict):
        raise ValueError("Anthropic response must be a JSON object")
    text_parts: list[str] = []
    tool_requests: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_requests.append(
                {
                    "id": block.get("id"),
                    "name": block.get("name", ""),
                    "arguments": block.get("input") if isinstance(block.get("input"), dict) else {},
                }
            )
    usage: dict[str, int] = {}
    _merge_usage(usage, data.get("usage"))
    return ModelResponse(
        content="".join(text_parts),
        tool_requests=tool_requests,
        finish_reason=str(data.get("stop_reason") or "stop"),
        usage=usage,
        metadata={"id": data.get("id"), "model": data.get("model")},
    )


__all__ = ["AnthropicConfig", "AnthropicProvider"]
