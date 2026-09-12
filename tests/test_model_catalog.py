"""Tests for the versioned model-alias catalog contract."""

import json

import httpx
import pytest

from micro_agent.models import (
    MODEL_CATALOG_API_VERSION,
    CatalogError,
    HttpModelCatalog,
    InMemoryModelCatalog,
    ModelCatalogEntry,
)


def test_in_memory_catalog_resolves_immutable_entries():
    entry = ModelCatalogEntry(
        alias="reasoning",
        provider="anthropic",
        model_id="claude-test",
        endpoint="https://api.anthropic.test",
    )
    catalog = InMemoryModelCatalog([entry])
    assert catalog.resolve("reasoning") == entry
    assert catalog.resolve("missing") is None


def test_http_catalog_sends_versioned_alias_and_authenticates():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "api_version": MODEL_CATALOG_API_VERSION,
                "model": {
                    "alias": "reasoning",
                    "provider": "anthropic",
                    "model_id": "claude-test",
                    "endpoint": "https://api.anthropic.test",
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        entry = HttpModelCatalog(
            "https://catalog.example.test/resolve",
            token="catalog-token",
            client=client,
        ).resolve("reasoning")
    finally:
        client.close()
    assert entry is not None
    assert entry.provider == "anthropic"
    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.headers["authorization"] == "Bearer catalog-token"
    assert json.loads(request.content) == {
        "api_version": MODEL_CATALOG_API_VERSION,
        "alias": "reasoning",
    }


def test_http_catalog_404_is_missing_and_invalid_contract_fails_closed():
    not_found = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    try:
        assert (
            HttpModelCatalog("https://catalog.example.test/resolve", client=not_found).resolve(
                "missing"
            )
            is None
        )
    finally:
        not_found.close()

    invalid = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "api_version": "old",
                    "model": {"alias": "reasoning", "provider": "fake", "model_id": "x"},
                },
            )
        )
    )
    try:
        with pytest.raises(CatalogError, match="invalid response"):
            HttpModelCatalog("https://catalog.example.test/resolve", client=invalid).resolve(
                "reasoning"
            )
    finally:
        invalid.close()


def test_catalog_rejects_remote_http_and_alias_mismatch():
    with pytest.raises(ValueError, match="HTTPS"):
        HttpModelCatalog("http://catalog.example.test/resolve")

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "api_version": MODEL_CATALOG_API_VERSION,
                    "model": {"alias": "other", "provider": "fake", "model_id": "x"},
                },
            )
        )
    )
    try:
        with pytest.raises(CatalogError, match="does not match"):
            HttpModelCatalog("https://catalog.example.test/resolve", client=client).resolve(
                "reasoning"
            )
    finally:
        client.close()
