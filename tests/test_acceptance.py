"""Acceptance tests: real network service and multi-replica shared sessions."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.interoperability.a2a import a2a_well_known_path
from micro_agent.models import FakeModelConfig, OpenAICompatConfig, OpenAICompatProvider
from micro_agent.session import InMemorySessionProvider, SqliteSessionProvider
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


class TestLiveOpenAICompatTranscript:
    """M13 acceptance: a live provider round-trips and replays tool history."""

    @pytest.mark.asyncio
    async def test_multiturn_tool_call_replays_from_session_storage(self):
        app = FastAPI()
        requests: list[list[dict]] = []

        @app.get("/v1/models")
        async def models() -> dict[str, list[object]]:
            return {"data": []}

        @app.post("/v1/chat/completions")
        async def completions(request: Request) -> dict:
            payload = await request.json()
            messages = payload["messages"]
            assert payload["model"] == "live-model"
            requests.append(messages)
            user_message = next(
                message for message in reversed(messages) if message.get("role") == "user"
            )
            user_payload = json.loads(user_message["content"])

            if user_payload.get("message") == "replay":
                assistant_tool_call = next(
                    message for message in messages if message.get("tool_calls")
                )
                assert assistant_tool_call["tool_calls"][0]["id"] == "call_live_1"
                assert any(
                    message.get("role") == "tool" and message.get("tool_call_id") == "call_live_1"
                    for message in messages
                )
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "transcript replayed",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }

            if any(message.get("role") == "tool" for message in messages):
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": "live tool complete",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }

            return {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_live_1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": '{"message":"live"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            }

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        task = asyncio.get_running_loop().create_task(server.serve())
        runtime: AdkRuntime | None = None
        agent = None
        try:
            while not server.started:
                await asyncio.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1]
            endpoint = f"http://127.0.0.1:{port}/v1"
            definition = load_definition_from_dict(
                {
                    "apiVersion": "microagents.io/v1alpha1",
                    "kind": "MicroAgent",
                    "metadata": {"name": "live-openai-agent", "version": "1.0.0"},
                    "spec": {
                        "behavior": {"instructions": "Use the echo tool."},
                        "dependencies": {
                            "model": {
                                "ref": "live-model",
                                "model_id": "live-model",
                                "provider": "openai-compatible",
                                "endpoint": endpoint,
                            },
                            "tools": [{"name": "echo", "source": "native"}],
                            "session": {"ttl_seconds": 3600},
                        },
                    },
                }
            )
            provider = OpenAICompatProvider(
                OpenAICompatConfig(endpoint=endpoint, model_id="live-model", trust_env=False)
            )
            sessions = InMemorySessionProvider()
            runtime = AdkRuntime(
                AdkRuntimeConfig(model_provider=provider, session_provider=sessions)
            )
            agent = DefaultMicroAgent(definition, runtime)
            await agent.initialize()
            await agent.start()

            from micro_agent.core import AgentRequest

            first = await agent.invoke(
                AgentRequest(input={"message": "first"}, session_id="live-session")
            )
            assert first.status == "success"
            assert first.output["content"] == "live tool complete"

            second = await agent.invoke(
                AgentRequest(input={"message": "replay"}, session_id="live-session")
            )
            assert second.status == "success"
            assert second.output["content"] == "transcript replayed"
            assert len(requests) == 3
            replay_request = requests[-1]
            assert any(message.get("tool_calls") for message in replay_request)
            assert any(
                message.get("role") == "tool" and message.get("tool_call_id") == "call_live_1"
                for message in replay_request
            )

            session = await sessions.get("live-session")
            assert session is not None
            assert any(message.get("tool_calls") for message in session.messages)
            assert any(
                message.get("role") == "tool" and message.get("tool_call_id") == "call_live_1"
                for message in session.messages
            )
        finally:
            if agent is not None:
                await agent.stop()
                await agent.shutdown()
            if runtime is not None:
                await runtime.close()
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
