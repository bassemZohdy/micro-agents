"""Versioned model-alias catalog contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

MODEL_CATALOG_API_VERSION = "microagents.io/model-catalog/v1"


@dataclass(frozen=True)
class ModelCatalogEntry:
    """Provider metadata resolved for one portable model alias."""

    alias: str
    provider: str
    model_id: str
    endpoint: str | None = None
    credential_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("alias", "provider", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"model catalog {name} must be a non-empty string")
        if self.endpoint is not None:
            _validate_endpoint(self.endpoint)
        if self.credential_ref is not None and (
            not isinstance(self.credential_ref, str) or not self.credential_ref.strip()
        ):
            raise ValueError("model catalog credential_ref must be a non-empty string")


class ModelCatalog(ABC):
    """Resolves a portable model alias to provider-specific metadata."""

    @abstractmethod
    def resolve(self, alias: str) -> ModelCatalogEntry | None:
        """Return the entry for ``alias``, or ``None`` when it is unknown."""


class InMemoryModelCatalog(ModelCatalog):
    """Deterministic catalog for embedding and tests."""

    def __init__(self, entries: Iterable[ModelCatalogEntry] | Mapping[str, ModelCatalogEntry]):
        values = list(entries.values()) if isinstance(entries, Mapping) else list(entries)
        self._entries: dict[str, ModelCatalogEntry] = {}
        for entry in values:
            if entry.alias in self._entries:
                raise ValueError(f"duplicate model catalog alias '{entry.alias}'")
            self._entries[entry.alias] = entry

    def resolve(self, alias: str) -> ModelCatalogEntry | None:
        """Return a copy-safe immutable catalog entry."""
        return self._entries.get(alias)


class CatalogError(RuntimeError):
    """Raised when a configured catalog cannot return a valid response."""


class HttpModelCatalog(ModelCatalog):
    """Resolve aliases through the versioned model-catalog HTTP contract.

    The request is ``POST <endpoint>`` with
    ``{"api_version": "microagents.io/model-catalog/v1", "alias": "..."}``.
    A successful response is
    ``{"api_version": "...", "model": {"alias": ..., "provider": ...,"model_id": ...}}``.
    A 404 means the alias is absent. Owned clients disable ambient proxies and
    redirects; injected clients stay owned by the caller.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        _validate_endpoint(endpoint)
        if timeout_seconds <= 0:
            raise ValueError("model catalog timeout must be greater than zero")
        self._endpoint = endpoint
        self._owns_client = client is None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._headers = headers
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            trust_env=False,
            verify=True,
            follow_redirects=False,
            headers=headers,
        )

    def resolve(self, alias: str) -> ModelCatalogEntry | None:
        """Fetch one alias and validate the complete versioned response."""
        if not alias.strip():
            raise CatalogError("model alias must not be empty")
        try:
            response = self._client.post(
                self._endpoint,
                json={"api_version": MODEL_CATALOG_API_VERSION, "alias": alias},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise CatalogError("model catalog request failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise CatalogError(f"model catalog returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CatalogError("model catalog returned invalid JSON") from exc
        try:
            entry = _parse_catalog_response(payload)
        except (TypeError, ValueError, CatalogSchemaError) as exc:
            raise CatalogError("model catalog returned an invalid response") from exc
        if entry.alias != alias:
            raise CatalogError("model catalog response alias does not match the request")
        return entry

    def close(self) -> None:
        """Close an owned HTTP client; injected clients remain caller-owned."""
        if self._owns_client:
            self._client.close()


class CatalogSchemaError(ValueError):
    """Raised when a catalog response violates the versioned contract."""


def _parse_catalog_response(payload: Any) -> ModelCatalogEntry:
    if not isinstance(payload, Mapping):
        raise CatalogSchemaError("catalog response must be an object")
    if set(payload) != {"api_version", "model"}:
        raise CatalogSchemaError("catalog response fields are not exact")
    if payload["api_version"] != MODEL_CATALOG_API_VERSION:
        raise CatalogSchemaError("unsupported catalog API version")
    model = payload["model"]
    if not isinstance(model, Mapping):
        raise CatalogSchemaError("catalog model must be an object")
    fields = {"alias", "provider", "model_id", "endpoint", "credential_ref"}
    if set(model) - fields:
        raise CatalogSchemaError("catalog model contains unknown fields")
    required = {"alias", "provider", "model_id"}
    if not required <= set(model):
        raise CatalogSchemaError("catalog model is missing required fields")
    return ModelCatalogEntry(
        alias=model["alias"],
        provider=model["provider"],
        model_id=model["model_id"],
        endpoint=model.get("endpoint"),
        credential_ref=model.get("credential_ref"),
    )


def _validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "model catalog endpoint must be an absolute http(s) URL without credentials, "
            "query, or fragment"
        )
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("model catalog endpoint must use HTTPS unless it targets loopback")


__all__ = [
    "CatalogError",
    "CatalogSchemaError",
    "HttpModelCatalog",
    "InMemoryModelCatalog",
    "MODEL_CATALOG_API_VERSION",
    "ModelCatalog",
    "ModelCatalogEntry",
]
