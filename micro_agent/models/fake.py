"""Deterministic fake model for testing and CI.

No paid model access required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from micro_agent.models.model import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ModelStreamEvent,
    ProviderCapabilities,
)


@dataclass
class FakeModelConfig:
    """Configuration for the fake model behavior."""

    response: str = "fake response"
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    should_error: bool = False
    error_message: str = "fake model error"
    stream_chunks: list[str] | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 10, "completion_tokens": 5}
    )


class FakeModelProvider(ModelProvider):
    """Deterministic fake model for testing.

    Returns configured responses without any external calls.
    """

    def __init__(self, config: FakeModelConfig | None = None) -> None:
        self._config = config or FakeModelConfig()
        self._invocations: list[dict[str, Any]] = []

    @property
    def invocations(self) -> list[dict[str, Any]]:
        """Return all recorded invocations."""
        return self._invocations

    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Return a deterministic response."""
        self._invocations.append(
            {
                "config": config,
                "messages": messages,
                "tools": tools,
            }
        )

        if self._config.should_error:
            raise RuntimeError(self._config.error_message)

        return ModelResponse(
            content=self._config.response,
            tool_requests=list(self._config.tool_requests),
            finish_reason="stop",
            usage=dict(self._config.usage),
        )

    async def stream(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Emit configured deterministic chunks and a complete final response."""
        if self._config.stream_chunks is None:
            raise NotImplementedError("fake streaming is not configured")
        self._invocations.append({"config": config, "messages": messages, "tools": tools})
        if self._config.should_error:
            raise RuntimeError(self._config.error_message)
        content = ""
        for chunk in self._config.stream_chunks:
            content += chunk
            yield ModelStreamEvent(delta=chunk)
        yield ModelStreamEvent(
            response=ModelResponse(
                content=content,
                tool_requests=list(self._config.tool_requests),
                finish_reason="stop",
                usage=dict(self._config.usage),
            )
        )

    def capabilities(self) -> ProviderCapabilities:
        """Report only features implemented by this configured fake provider."""
        return ProviderCapabilities(
            tool_use=True,
            streaming=self._config.stream_chunks is not None,
        )

    async def health_check(self) -> bool:
        """Always healthy."""
        return True
