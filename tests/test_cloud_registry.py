"""Cloud C1 tests: descriptors, the minimal registry, and discovery."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from cloud.descriptors import (
    DESCRIPTOR_SCHEMA_VERSION,
    AgentDescriptor,
    DescriptorCardMismatchError,
    DescriptorError,
    SkillDescriptor,
    descriptor_from_definition,
)
from cloud.discovery import RegistryDiscoveryClient, RegistryUnreachableError
from cloud.registry import InMemoryAgentRegistry, UnknownAgentError, create_registry_app
from micro_agent.definition import load_definition_from_dict


def _definition(**overrides: object) -> object:
    metadata: dict[str, object] = {
        "name": "greeter",
        "version": "1.0.0",
        "description": "Greets callers.",
        "labels": {"team": "platform"},
        **overrides,
    }
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": metadata,
            "spec": {
                "behavior": {"instructions": "Greet politely."},
                "dependencies": {
                    "memory": {"ref": "agent-memory"},
                    "tools": [{"name": "clock", "side_effect": "read_only"}],
                    "skills": [
                        {
                            "id": "greet",
                            "name": "Greet",
                            "description": "Produce a greeting.",
                            "tags": ["social"],
                        }
                    ],
                },
            },
        }
    )


def _card(name: str = "greeter", version: str = "1.0.0") -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "protocol_version": "0.3.0",
        "skills": [{"id": "greet", "name": "Greet"}],
    }


class TestDescriptors:
    def test_descriptor_is_derived_from_definition_and_card(self):
        definition = _definition()
        descriptor = descriptor_from_definition(
            definition, card_url="http://greeter/.well-known/agent-card.json", card=_card()
        )
        assert descriptor.schema_version == DESCRIPTOR_SCHEMA_VERSION
        assert descriptor.name == "greeter"
        assert descriptor.version == "1.0.0"
        assert [skill.id for skill in descriptor.skills] == ["greet"]
        assert descriptor.capabilities["memory"] is True
        assert descriptor.capabilities["tools"] is True
        assert descriptor.capabilities["session"] is False
        assert descriptor.card_fingerprint
        assert descriptor.labels == {"team": "platform"}

    def test_card_contradiction_is_rejected(self):
        with pytest.raises(DescriptorCardMismatchError, match="version"):
            descriptor_from_definition(
                _definition(), card_url="http://greeter", card=_card(version="9.9.9")
            )
        with pytest.raises(DescriptorCardMismatchError, match="unknown skill"):
            descriptor_from_definition(
                _definition(),
                card_url="http://greeter",
                card={**_card(), "skills": [{"id": "ghost"}]},
            )

    def test_descriptor_roundtrips_through_dict_and_rejects_unknown_schema(self):
        descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
        restored = AgentDescriptor.from_dict(descriptor.to_dict())
        assert restored == descriptor
        with pytest.raises(DescriptorError, match="schema version"):
            AgentDescriptor.from_dict({**descriptor.to_dict(), "schema_version": "v9"})

    async def test_descriptor_requires_name_and_version(self):
        registry = InMemoryAgentRegistry()
        with pytest.raises(DescriptorError, match="name and version"):
            await registry.register(AgentDescriptor(name="", version="1.0.0"))


class TestRegistry:
    async def test_register_query_and_lease_expiry(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr("cloud.registry.time.monotonic", lambda: clock[0])
        registry = InMemoryAgentRegistry(default_lease_seconds=60)
        await registry.register(
            AgentDescriptor(
                name="greeter",
                version="1.0.0",
                visibility=["acme"],
                skills=[SkillDescriptor(id="greet", name="Greet")],
            )
        )
        await registry.register(AgentDescriptor(name="echo", version="2.0.0"))

        assert len(await registry.query()) == 2
        assert [e.descriptor.name for e in await registry.query(skill="greet")] == ["greeter"]
        # Empty visibility is unrestricted, so 'echo' is visible to every tenant.
        assert {e.descriptor.name for e in await registry.query(tenant="acme")} == {
            "echo",
            "greeter",
        }
        assert {e.descriptor.name for e in await registry.query(tenant="other")} == {"echo"}

        clock[0] += 61
        entries = await registry.query()
        assert all(not e.healthy for e in entries)
        assert [e.descriptor.name for e in await registry.query(healthy_only=True)] == []

    async def test_heartbeat_renews_and_unknown_agent_fails(self):
        registry = InMemoryAgentRegistry(default_lease_seconds=60)
        with pytest.raises(UnknownAgentError):
            await registry.heartbeat("ghost", "1.0.0")
        await registry.register(AgentDescriptor(name="greeter", version="1.0.0"))
        entry = await registry.heartbeat("greeter", "1.0.0")
        assert entry.healthy
        with pytest.raises(UnknownAgentError):
            await registry.deregister("ghost", "1.0.0")
        await registry.deregister("greeter", "1.0.0")
        assert await registry.query() == []


class TestRegistryHttp:
    def _client(self) -> TestClient:
        return TestClient(create_registry_app())

    def test_http_registration_query_and_heartbeat(self):
        with self._client() as http:
            descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
            response = http.put("/registry/agents/greeter/1.0.0", json=descriptor.to_dict())
            assert response.status_code == 200
            assert response.json()["healthy"] is True

            found = http.get("/registry/agents", params={"skill": "greet"}).json()
            assert [a["descriptor"]["name"] for a in found["agents"]] == ["greeter"]

            beat = http.post("/registry/agents/greeter/1.0.0/heartbeat")
            assert beat.status_code == 200

            assert http.delete("/registry/agents/greeter/1.0.0").status_code == 200
            assert http.get("/registry/agents/greeter/1.0.0").status_code == 404

    def test_http_rejects_mismatched_payload_identity(self):
        with self._client() as http:
            descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
            response = http.put("/registry/agents/other-name/1.0.0", json=descriptor.to_dict())
            assert response.status_code == 422
            assert "must match the URL path" in response.json()["detail"]


class TestDiscovery:
    async def test_discover_uses_registry_and_degrades_to_cache(self):
        app = create_registry_app()
        asgi = httpx.ASGITransport(app=app)
        state = {"down": False}

        async def handler(request: httpx.Request) -> httpx.Response:
            if state["down"]:
                return httpx.Response(503, json={"detail": "down"})
            return await asgi.handle_async_request(request)

        discovery = RegistryDiscoveryClient(
            "http://registry.test",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=""),
        )
        try:
            descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
            await discovery.register(descriptor)
            hits = await discovery.discover(skill="greet")
            assert len(hits) == 1
            assert hits[0].healthy and not hits[0].from_cache
            assert hits[0].descriptor.name == "greeter"

            # Registry outage on the SAME client: the cached snapshot for
            # this exact query is served stale rather than failing.
            state["down"] = True
            stale = await discovery.discover(skill="greet")
            assert stale and all(agent.from_cache for agent in stale)
            with pytest.raises(RegistryUnreachableError):
                await discovery.discover(name="never-cached")
        finally:
            await discovery.aclose()

    async def test_register_error_surfaces_descriptor_problem(self):
        transport = httpx.ASGITransport(app=create_registry_app())
        discovery = RegistryDiscoveryClient(
            "http://registry.test",
            client=httpx.AsyncClient(transport=transport, base_url=""),
        )
        try:
            with pytest.raises(DescriptorError, match="name and version"):
                await discovery.register(AgentDescriptor(name="", version="1.0.0"))
        finally:
            await discovery.aclose()


class TestRegistryHardening:
    """Verification for the cloud hardening batch (registration/heartbeat TTLs,
    clean 404 details, and the documented query ordering)."""

    def _registered_client(self) -> TestClient:
        client = TestClient(create_registry_app())
        descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
        response = client.put(
            "/registry/agents/greeter/1.0.0",
            json=descriptor.to_dict(),
            params={"ttl_seconds": 30},
        )
        assert response.status_code == 200
        return client

    def test_ttl_is_accepted_on_register_and_heartbeat(self):
        with self._registered_client() as http:
            beat = http.post(
                "/registry/agents/greeter/1.0.0/heartbeat",
                params={"ttl_seconds": 45},
            )
            assert beat.status_code == 200

    def test_non_positive_heartbeat_ttl_is_rejected(self):
        with self._registered_client() as http:
            for ttl in ("0", "-5"):
                beat = http.post(
                    "/registry/agents/greeter/1.0.0/heartbeat",
                    params={"ttl_seconds": ttl},
                )
                assert beat.status_code == 422
                assert "positive" in beat.json()["detail"]

    def test_404_details_are_clean_without_keyerror_quotes(self):
        with TestClient(create_registry_app()) as http:
            for method, url in (
                ("get", "/registry/agents/ghost/1.0.0"),
                ("post", "/registry/agents/ghost/1.0.0/heartbeat"),
                ("delete", "/registry/agents/ghost/1.0.0"),
            ):
                response = getattr(http, method)(url)
                assert response.status_code == 404
                detail = response.json()["detail"]
                assert detail == "ghost@1.0.0 is not registered"
                assert "KeyError" not in detail
                assert "'" not in detail

    def test_query_results_ordered_by_name_then_version(self):
        """The documented contract: (agent name, version) order, regardless of
        registration order."""
        with TestClient(create_registry_app()) as http:
            for name, version in (
                ("beta", "1.10.0"),
                ("alpha", "1.0.0"),
                ("beta", "1.2.0"),
                ("alpha", "2.0.0"),
            ):
                descriptor = descriptor_from_definition(
                    _definition(name=name, version=version), card_url="http://x.test"
                )
                assert (
                    http.put(
                        f"/registry/agents/{name}/{version}", json=descriptor.to_dict()
                    ).status_code
                    == 200
                )

            found = http.get("/registry/agents").json()["agents"]
            assert [(a["descriptor"]["name"], a["descriptor"]["version"]) for a in found] == [
                ("alpha", "1.0.0"),
                ("alpha", "2.0.0"),
                ("beta", "1.10.0"),
                ("beta", "1.2.0"),
            ]


class TestDiscoveryHardening:
    async def test_client_side_errors_are_authoritative_not_cached(self):
        """A 4xx from the registry is an answer, not an outage: it must
        propagate even when a cached snapshot for the query exists."""
        app = create_registry_app()
        asgi = httpx.ASGITransport(app=app)
        state = {"reject": False}

        async def handler(request: httpx.Request) -> httpx.Response:
            if state["reject"]:
                return httpx.Response(404, json={"detail": "no such query"})
            return await asgi.handle_async_request(request)

        discovery = RegistryDiscoveryClient(
            "http://registry.test",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=""),
        )
        try:
            descriptor = descriptor_from_definition(_definition(), card_url="http://greeter")
            await discovery.register(descriptor)
            hits = await discovery.discover(skill="greet")
            assert hits and not hits[0].from_cache  # snapshot now cached

            state["reject"] = True
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await discovery.discover(skill="greet")
            assert exc_info.value.response.status_code == 404
        finally:
            await discovery.aclose()
