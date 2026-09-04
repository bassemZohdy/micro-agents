"""Cloud C2 tests: the versioned config store, HTTP surface, and client."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.config import (
    ConfigValidationError,
    EnvironmentSecretResolver,
    InMemoryConfigStore,
    create_config_app,
)
from cloud.config_client import ConfigClient, ConfigPlaneUnreachableError


def _definition_payload(name: str = "greeter", version: str = "1.0.0") -> dict[str, object]:
    return {
        "apiVersion": "microagents.io/v1alpha1",
        "kind": "MicroAgent",
        "metadata": {"name": name, "version": version},
        "spec": {"behavior": {"instructions": "Greet politely."}},
    }


class TestConfigStore:
    async def test_versions_are_append_only_and_validated(self):
        store = InMemoryConfigStore()
        first = await store.store_definition("greeter", _definition_payload())
        second = await store.store_definition("greeter", _definition_payload(version="1.1.0"))
        assert (first.version, second.version) == (1, 2)
        assert second.digest != first.digest

        with pytest.raises(ConfigValidationError, match="invalid definition"):
            await store.store_definition("greeter", {"kind": "not-a-definition"})
        with pytest.raises(ConfigValidationError, match="invalid overlay"):
            await store.store_overlay("greeter", {"model_endpoint": "not-a-url"})
        assert len(await store.history("greeter", "definition")) == 2

    async def test_get_latest_pinned_and_rollback(self):
        store = InMemoryConfigStore()
        await store.store_definition("greeter", _definition_payload(version="1.0.0"))
        await store.store_definition("greeter", _definition_payload(version="1.1.0"))

        latest = await store.get("greeter", "definition")
        pinned = await store.get("greeter", "definition", version=1)
        assert latest.payload["metadata"]["version"] == "1.1.0"
        assert pinned.payload["metadata"]["version"] == "1.0.0"

        rolled = await store.rollback("greeter", "definition", to_version=1)
        assert rolled.version == 3  # rollback appends, never rewrites
        assert (await store.get("greeter", "definition")).payload["metadata"]["version"] == "1.0.0"
        assert len(await store.history("greeter", "definition")) == 3

        with pytest.raises(KeyError):
            await store.get("ghost", "definition")
        with pytest.raises(KeyError):
            await store.get("greeter", "definition", version=99)

    async def test_overlay_validation_and_secret_reference_contract(self):
        store = InMemoryConfigStore()
        overlay = await store.store_overlay(
            "greeter",
            {
                "model_endpoint": "https://model.example.test/v1",
                "memory_endpoint": "postgres://db.example.test/agents",
            },
        )
        assert overlay.kind == "overlay"
        # The overlay carries endpoints only; secrets stay credential refs
        # resolved at use time from the deployment environment.
        resolver = EnvironmentSecretResolver()
        assert resolver.resolve("PATH") is not None
        assert resolver.resolve("DEFINITELY_NOT_SET_12345") is None


class TestConfigHttp:
    def test_http_roundtrip_validation_and_rollback(self):
        with TestClient(create_config_app()) as http:
            ok = http.put("/config/agents/greeter/definition", json=_definition_payload())
            assert ok.status_code == 200
            assert ok.json()["version"] == 1

            bad = http.put("/config/agents/greeter/definition", json={"kind": "nope"})
            assert bad.status_code == 422
            assert "invalid definition" in bad.json()["detail"]

            bad_overlay = http.put(
                "/config/agents/greeter/overlay", json={"model_endpoint": "not-a-url"}
            )
            assert bad_overlay.status_code == 422
            overlay_ok = http.put(
                "/config/agents/greeter/overlay",
                json={"model_endpoint": "https://model.example.test/v1"},
            )
            assert overlay_ok.status_code == 200

            fetched = http.get("/config/agents/greeter/definition").json()
            assert fetched["payload"]["metadata"]["name"] == "greeter"
            pinned = http.get("/config/agents/greeter/definition", params={"version": 1}).json()
            assert pinned["version"] == 1
            assert http.get("/config/agents/ghost/definition").status_code == 404

            history = http.get("/config/agents/greeter/history").json()
            assert [v["version"] for v in history["versions"]] == [1]

            rolled = http.post(
                "/config/agents/greeter/rollback", json={"kind": "definition", "to_version": 1}
            )
            assert rolled.status_code == 200
            assert rolled.json()["version"] == 2

            bad_rollback = http.post(
                "/config/agents/greeter/rollback", json={"kind": "definition", "to_version": 77}
            )
            assert bad_rollback.status_code == 404


class TestConfigClient:
    async def test_client_degrades_to_last_good_payload(self):
        app = create_config_app()
        asgi = httpx.ASGITransport(app=app)
        state = {"down": False}

        async def handler(request: httpx.Request) -> httpx.Response:
            if state["down"]:
                return httpx.Response(503, json={"detail": "down"})
            return await asgi.handle_async_request(request)

        client = ConfigClient(
            "http://config.test",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=""),
        )
        try:
            await client.put_definition("greeter", _definition_payload())
            got = await client.get("greeter", "definition")
            assert got["payload"]["metadata"]["version"] == "1.0.0"
            assert not got.get("from_cache", False)

            state["down"] = True
            stale = await client.get("greeter", "definition")
            assert stale["from_cache"] is True
            assert stale["payload"]["metadata"]["version"] == "1.0.0"
            with pytest.raises(ConfigPlaneUnreachableError):
                await client.get("ghost", "definition")
            with pytest.raises(ValueError, match="kind"):
                await client.get("greeter", "secrets")
        finally:
            await client.aclose()
