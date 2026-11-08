"""Azure OpenAI model provider.

Azure OpenAI serves the OpenAI chat-completions wire format, but addresses a
*deployment* instead of a model name, requires an ``api-version`` query
parameter, and authenticates with an ``api-key`` header. This adapter reuses
the OpenAI-compatible request/response translation and changes only those
three things, so tool calling, streaming and structured output behave
identically. Microsoft Entra ID (bearer) authentication is out of scope; inject
an ``http_client`` that signs requests if it is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from micro_agent.models.model import ModelConfig
from micro_agent.models.openai_compat import OpenAICompatConfig, OpenAICompatProvider

DEFAULT_AZURE_API_VERSION = "2024-10-21"


@dataclass
class AzureOpenAIConfig(OpenAICompatConfig):
    """Connection settings for an Azure OpenAI resource.

    ``endpoint`` is the resource root (``https://<name>.openai.azure.com``) and
    ``model_id`` is the *deployment name*.
    """

    api_version: str = DEFAULT_AZURE_API_VERSION


class AzureOpenAIProvider(OpenAICompatProvider):
    """Model provider backed by an Azure OpenAI deployment."""

    def __init__(self, config: AzureOpenAIConfig) -> None:
        if not config.endpoint:
            raise ValueError("Azure OpenAI endpoint is required")
        if not config.model_id or config.model_id == "default":
            raise ValueError("Azure OpenAI deployment name (model_id) is required")
        if not config.api_version:
            raise ValueError("Azure OpenAI api_version is required")
        if config.timeout_seconds <= 0:
            raise ValueError("Azure OpenAI timeout must be greater than zero")
        self._azure = config
        headers = dict(config.default_headers)
        if config.api_key:
            headers["api-key"] = config.api_key
        # The Bearer header the base class would add is wrong for Azure keys.
        super().__init__(
            OpenAICompatConfig(
                endpoint=config.endpoint,
                model_id=config.model_id,
                api_key=None,
                timeout_seconds=config.timeout_seconds,
                default_headers=headers,
                trust_env=config.trust_env,
                verify_tls=config.verify_tls,
                proxy=config.proxy,
                http_client=config.http_client,
                telemetry=config.telemetry,
            )
        )

    def _chat_url(self, config: ModelConfig) -> str:
        deployment = quote(config.model_id or self._azure.model_id, safe="")
        return (
            f"{self._endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={quote(self._azure.api_version, safe='')}"
        )

    def _models_url(self) -> str:
        version = quote(self._azure.api_version, safe="")
        return f"{self._endpoint}/openai/models?api-version={version}"


__all__ = ["DEFAULT_AZURE_API_VERSION", "AzureOpenAIConfig", "AzureOpenAIProvider"]
