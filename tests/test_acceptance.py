"""Acceptance tests: real network service and multi-replica shared sessions."""

import asyncio
from pathlib import Path

import httpx
import pytest
import uvicorn

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.interoperability.a2a import a2a_well_known_path
from micro_agent.models import FakeModelConfig
from micro_agent.session import SqliteSessionProvider
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


def _definition() -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "net-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Network test agent."},
                "dependencies": {"model": {"ref": "fake-model"}},
            },
        }
    )


async def _app():
    definition = _definition()
    runtime = AdkRuntime(
        AdkRuntimeConfig(fake_model_config=FakeModelConfig(response="hello over http"))
    )
    agent = DefaultMicroAgent(definition, runtime)
    await agent.initialize()
    await agent.start()
    return create_app(agent)


class TestRealNetworkService:
    """M15 acceptance: the agent runs as an independent network service.

    Boots uvicorn on a real socket (ephemeral port) and talks HTTP — no
    in-process ASGI transport.
    """

    @pytest.mark.asyncio
    async def test_live_service_endpoints(self):
        server = uvicorn.Server(
            uvicorn.Config(await _app(), host="127.0.0.1", port=0, log_level="error")
        )
        task = asyncio.get_running_loop().create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1]
            base = f"http://127.0.0.1:{port}"

            async with httpx.AsyncClient(base_url=base, timeout=10.0, trust_env=False) as client:
                invoke = await client.post(
                    "/v1/invoke", json={"input": {"q": "hi"}, "request_id": "net-1"}
                )
                assert invoke.status_code == 200
                body = invoke.json()
                assert body["status"] == "success"
                assert body["output"]["content"] == "hello over http"
                assert body["request_id"] == "net-1"

                live = await client.get("/health/live")
                assert live.status_code == 200
                assert live.json()["status"] == "healthy"

                ready = await client.get("/health/ready")
                assert ready.status_code == 200
                assert ready.json()["details"]["ready"] is True

                card = await client.get(a2a_well_known_path())
                assert card.status_code == 200
                assert card.json()["name"] == "net-agent"
        finally:
            server.should_exit = True
            await task


class TestMultiReplicaSharedSession:
    """M11 acceptance: replicas share persistent session state."""

    @pytest.mark.asyncio
    async def test_two_replicas_share_session_state(self, tmp_path: Path):
        db_path = str(tmp_path / "sessions.db")
        replica_a = SqliteSessionProvider(db_path)
        replica_b = SqliteSessionProvider(db_path)

        session = await replica_a.create("shared-session")
        session.messages.append({"role": "user", "content": "from replica A"})
        await replica_a.update(session)

        # A different "replica" (separate provider/connection) sees the state.
        seen_by_b = await replica_b.get("shared-session")
        assert seen_by_b is not None
        assert seen_by_b.messages[-1]["content"] == "from replica A"

        seen_by_b.messages.append({"role": "assistant", "content": "from replica B"})
        await replica_b.update(seen_by_b)

        reread_by_a = await replica_a.get("shared-session")
        assert [m["content"] for m in reread_by_a.messages] == [
            "from replica A",
            "from replica B",
        ]
        assert (await replica_a.list_active())[0].session_id == "shared-session"

        await replica_a.delete("shared-session")
        assert await replica_b.get("shared-session") is None
        await replica_a.aclose()
        await replica_b.aclose()

    @pytest.mark.asyncio
    async def test_sqlite_provider_expiration(self, tmp_path: Path):
        provider = SqliteSessionProvider(str(tmp_path / "sessions.db"))
        await provider.create("expiring", ttl_seconds=0)
        assert await provider.get("expiring") is None
        assert await provider.list_active() == []
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_runtime_uses_persistent_session(self, tmp_path: Path):
        """AdkRuntime with a SQLite provider persists sessions across agents."""
        db_path = str(tmp_path / "sessions.db")
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "sess-agent", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "x"},
                    "dependencies": {"model": {"ref": "fake-model"}},
                },
            }
        )

        async def invoke_with(runtime):
            agent = await runtime.create(definition)
            return await runtime.invoke(agent, AgentRequest(input={}, session_id="persist-1"))

        from micro_agent.core import AgentRequest

        first = AdkRuntime(
            AdkRuntimeConfig(
                fake_model_config=FakeModelConfig(response="one"),
                session_provider=SqliteSessionProvider(db_path),
            )
        )
        await invoke_with(first)
        await first.close()

        second = AdkRuntime(
            AdkRuntimeConfig(
                fake_model_config=FakeModelConfig(response="two"),
                session_provider=SqliteSessionProvider(db_path),
            )
        )
        await invoke_with(second)
        await second.close()

        store = SqliteSessionProvider(db_path)
        session = await store.get("persist-1")
        assert session is not None
        user_messages = [m for m in session.messages if m["role"] == "user"]
        assert len(user_messages) == 2
        await store.aclose()
