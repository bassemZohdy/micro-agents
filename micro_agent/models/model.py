"""Micro-Agent Model Support.

Model configuration and provider abstraction. No paid model required for CI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Model Configuration
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    """Model configuration from definition."""

    ref: str
    provider: str | None = None
    model_id: str | None = None
    endpoint: str | None = None
    credential_ref: str | None = None
    generation: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    capabilities: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Model Response
# ---------------------------------------------------------------------------


@dataclass
class ModelResponse:
    """Response from a model invocation."""

    content: str = ""
    tool_requests: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelStreamEvent:
    """One provider stream event; the final event carries the full response."""

    delta: str = ""
    response: ModelResponse | None = None


# ---------------------------------------------------------------------------
# Model Provider Interface
# ---------------------------------------------------------------------------


@dataclass
class ProviderCapabilities:
    """What a provider's wire protocol actually supports.

    Runtimes use this to negotiate: declaring tools against a provider that
    cannot call them fails at startup instead of silently dropping the tools.
    """

    tool_use: bool = False
    streaming: bool = False
    structured_output: bool = False


class ModelProvider(ABC):
    """Abstract model provider interface."""

    def capabilities(self) -> ProviderCapabilities:
        """Report what the provider's protocol supports; conservative default."""
        return ProviderCapabilities()

    @abstractmethod
    async def generate(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Generate a response from the model."""

    async def stream(
        self,
        config: ModelConfig,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream model output when ``capabilities().streaming`` is true."""
        raise NotImplementedError("model provider does not implement streaming")
        yield ModelStreamEvent()  # pragma: no cover - marks this as an async generator

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the model provider is available."""
