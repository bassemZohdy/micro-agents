"""Micro-Agent Models — model configuration and provider abstraction."""

from micro_agent.models.anthropic import AnthropicConfig, AnthropicProvider
from micro_agent.models.catalog import (
    MODEL_CATALOG_API_VERSION,
    CatalogError,
    CatalogSchemaError,
    HttpModelCatalog,
    InMemoryModelCatalog,
    ModelCatalog,
    ModelCatalogEntry,
)
from micro_agent.models.fake import FakeModelConfig, FakeModelProvider
from micro_agent.models.model import (
    ModelConfig,
    ModelProvider,
    ModelResponse,
    ModelStreamEvent,
    ProviderCapabilities,
)
from micro_agent.models.openai_compat import OpenAICompatConfig, OpenAICompatProvider
from micro_agent.models.structured import output_contract_json_schema, structured_output_generation

__all__ = [
    "AnthropicConfig",
    "AnthropicProvider",
    "CatalogError",
    "CatalogSchemaError",
    "FakeModelConfig",
    "FakeModelProvider",
    "HttpModelCatalog",
    "InMemoryModelCatalog",
    "MODEL_CATALOG_API_VERSION",
    "ModelConfig",
    "ModelProvider",
    "ModelResponse",
    "ModelStreamEvent",
    "OpenAICompatConfig",
    "OpenAICompatProvider",
    "ModelCatalog",
    "ModelCatalogEntry",
    "ProviderCapabilities",
    "output_contract_json_schema",
    "structured_output_generation",
]
