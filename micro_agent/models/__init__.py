"""Micro-Agent Models — model configuration and provider abstraction."""

from micro_agent.models.fake import FakeModelConfig, FakeModelProvider
from micro_agent.models.model import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ProviderCapabilities,
)
from micro_agent.models.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from micro_agent.models.structured import output_contract_json_schema, structured_output_generation

__all__ = [
    "FakeModelConfig",
    "FakeModelProvider",
    "ModelConfig",
    "ModelProvider",
    "ModelResponse",
    "OpenAICompatConfig",
    "OpenAICompatProvider",
    "ProviderCapabilities",
    "output_contract_json_schema",
    "structured_output_generation",
]
