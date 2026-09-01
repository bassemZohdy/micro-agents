"""OpenAI-compatible HTTP model provider.

Talks to an endpoint that implements the /chat/completions contract. The
provider is injectable through runtime configuration and selected by the
executable bootstrap when a live endpoint is configured. The fake provider
remains the CI default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from micro_agent.models.model import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ProviderCapabilities,
)
from micro_agent.observability import Telemetry


@dataclass
class OpenAICompatConfig:
    """Connection settings for an OpenAI-compatible endpoint.

    ``http_client`` is an injection seam for tests and deployments that own
    the transport; when omitted, the provider builds its own client honoring
    ``trust_env``, ``verify_tls``, and ``proxy``. A proxy is an explicit
    opt-in — ambient proxy environment variables are never trusted by
    default.
    """

    endpoint: str  # e.g. https://llm.example.com/v1
    model_id: str = "default"
    api_key: str | None = None
    timeout_seconds: float = 30.0
    default_headers: dict[str, str] = field(default_factory=dict)
    trust_env: bool = False
    verify_tls: bool = True
    proxy: str | None = None
    http_client: httpx.AsyncClient | None = None
    telemetry: Telemetry | None = None


class OpenAICompatProvider(ModelProvider):
    """Model provider backed by an OpenAI-compatible chat-completions endpoint."""

    def __init__(self, config: OpenAICompatConfig) -> None:
        self._config = config
        self._endpoint = config.endpoint.rstrip("/")
        self._owns_client = config.http_client is None
        headers = {"Content-Type": "application/json", **config.default_headers}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        # Keep a normalized base URL for relative use by callers while the
        # provider below uses absolute endpoint paths. This preserves any
        # configured prefix (for example ``/v1``) even for injected clients
        # whose own ``base_url`` lacks a trailing slash.
        self._client = config.http_client or httpx.AsyncClient(
            base_url=f"{config.endpoint.rstrip('/')}/",
            headers=headers,
            timeout=config.timeout_seconds,
            trust_env=config.trust_env,
            verify=config.verify_tls,
            proxy=config.proxy,
        )

    def _payload(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model_id or self._config.model_id,
            "messages": messages,
        }
        payload.update(config.generation)
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
                for tool in tools
            ]
        return payload

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate a response via POST /chat/completions."""
        headers: dict[str, str] = {}
        if self._config.telemetry is not None:
            self._config.telemetry.inject_context(headers)
        response = await self._client.post(
            f"{self._endpoint}/chat/completions",
            json=self._payload(config, messages, tools),
            headers=headers or None,
        )
        response.raise_for_status()
        data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        tool_calls = message.get("tool_calls") or []
        tool_requests = [
            {
                "id": call.get("id"),
                "name": call.get("function", {}).get("name", ""),
                "arguments": _parse_arguments(call.get("function", {}).get("arguments")),
            }
            for call in tool_calls
        ]
        return ModelResponse(
            content=message.get("content") or "",
            tool_requests=tool_requests,
            finish_reason=choice.get("finish_reason") or "stop",
            usage=dict(data.get("usage") or {}),
        )

    def capabilities(self) -> ProviderCapabilities:
        """Chat completions supports function calling; streaming and
        structured-output APIs are not wired yet."""
        return ProviderCapabilities(tool_use=True)

    async def health_check(self) -> bool:
        """GET /models as a cheap availability probe."""
        try:
            response = await self._client.get(f"{self._endpoint}/models")
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        """Release the underlying HTTP connection pool when we own it."""
        if self._owns_client:
            await self._client.aclose()


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments arrive as a JSON string per the wire contract."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        import json

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"raw": raw}
    return {}
