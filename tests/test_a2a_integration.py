"""A2A integration against the official v1.0.1 SDK client and server APIs."""

import asyncio

import httpx
import pytest

from micro_agent.core import DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.interoperability.a2a import (
    a2a_well_known_path,
    agent_card_from_definition,
    skills_mapping,
)
from runtimes.adk import AdkRuntime

pytestmark = pytest.mark.integration

pytest.importorskip("a2a")


def _definition() -> object:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {
                "name": "residency-renewal",
                "version": "1.0.0",
                "description": "Handles residency renewal activities.",
                "labels": {"domain": "residency"},
            },
            "spec": {
                "behavior": {"instructions": "Assist with renewals."},
                "dependencies": {
                    "model": {"ref": "fake-model"},
                    "skills": [
                        {
                            "id": "check-eligibility",
                            "name": "Check Eligibility",
                            "description": "Determine renewal eligibility.",
                            "tags": ["residency", "eligibility"],
                        },
                        {
                            "id": "submit-renewal",
                            "name": "Submit Renewal",
                            "description": "Submit a renewal application.",
                            "tags": ["residency"],
                        },
                    ],
                },
                "interoperability": {"a2a": {"enabled": True, "protocol_version": "1.0.1"}},
            },
        }
    )


class TestSkillsMapping:
    """Definition skills map to A2A AgentSkill shapes."""

    def test_skills_mapping(self):
        definition = _definition()
        skills = skills_mapping(definition)
        assert [s.id for s in skills] == ["check-eligibility", "submit-renewal"]
        assert skills[0].name == "Check Eligibility"
        assert skills[0].tags == ["residency", "eligibility"]


class TestAgentCardGeneration:
    """AgentCard generated from a definition."""

    def test_card_fields(self):
        definition = _definition()
        card = agent_card_from_definition(definition, base_url="https://agent.example.com")
        assert card.name == "residency-renewal"
        assert card.version == "1.0.0"
        assert card.supported_interfaces[0].url == "https://agent.example.com"
        assert card.supported_interfaces[0].protocol_version == "1.0.1"
        assert len(card.skills) == 2

    def test_card_url_falls_back_to_a2a_endpoint(self):
        definition = load_definition_from_dict(
            {
                "apiVersion": "microagents.io/v1alpha1",
                "kind": "MicroAgent",
                "metadata": {"name": "a", "version": "1.0.0"},
                "spec": {
                    "behavior": {"instructions": "x"},
                    "interoperability": {
                        "a2a": {"enabled": True, "endpoint": "https://a2a.example.com"}
                    },
                },
            }
        )
        card = agent_card_from_definition(definition)
        assert card.supported_interfaces[0].url == "https://a2a.example.com"


class TestAgentCardEndpoint:
    """The standard well-known agent-card endpoint serves the v1 card."""

    def _app(self):
        return create_app(
            DefaultMicroAgent(_definition(), AdkRuntime()), base_url="https://agent.example.com"
        )

    @pytest.mark.asyncio
    async def test_endpoint_serves_card(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(a2a_well_known_path())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_independent_client_validates_card(self):
        """A third-party client fetches and validates the card as raw JSON."""
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(a2a_well_known_path())
        card = response.json()

        # A2A agent-card contract checks (no framework types involved).
        assert card["name"] == "residency-renewal"
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0.1"
        assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
        assert card["supportedInterfaces"][0]["url"].startswith("https://")
        assert isinstance(card["capabilities"], dict)
        assert isinstance(card["skills"], list) and card["skills"]
        for skill in card["skills"]:
            assert skill["id"]
            assert skill["name"]
            assert isinstance(skill["tags"], list)
        assert card["defaultInputModes"] == ["application/json"]
        assert card["defaultOutputModes"] == ["application/json"]

    @pytest.mark.asyncio
    async def test_unsupported_protocol_version_is_rejected(self):
        app = self._app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/",
                headers={"X-A2A-Version": "0.3.0"},
                json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}},
            )
        assert response.status_code == 400
        assert response.json() == {
            "code": "unsupported_protocol_version",
            "message": "Protocol version '0.3.0' is not supported",
        }


class TestOfficialSdkInterop:
    """The official a2a-sdk client resolves the card and completes a task."""

    class _NoopPushSender:
        async def send_notification(self, task_id, event):
            return None

    @pytest.mark.asyncio
    async def test_official_client_resolves_card_and_completes_task(self, tmp_path):
        """An official SDK client resolves the card and completes a task."""
        import json as jsonlib

        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import (
            DeleteTaskPushNotificationConfigRequest,
            GetTaskPushNotificationConfigRequest,
            GetTaskRequest,
            ListTaskPushNotificationConfigsRequest,
            ListTasksRequest,
            Message,
            Part,
            Role,
            SendMessageConfiguration,
            SendMessageRequest,
            TaskPushNotificationConfig,
            TaskState,
        )
        from a2a.utils.errors import TaskNotFoundError

        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=FakeModelProvider(FakeModelConfig(response="renewal done"))
            )
        )
        agent = DefaultMicroAgent(_definition(), runtime)
        await agent.initialize()
        await agent.start()
        app = create_app(
            agent,
            base_url="http://test",
            a2a_store_path=str(tmp_path / "a2a.db"),
            a2a_push_sender=self._NoopPushSender(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            resolver = A2ACardResolver(httpx_client=http_client, base_url="http://test")
            card = await resolver.get_agent_card()
            assert card.name == "residency-renewal"

            config = ClientConfig(
                httpx_client=http_client,
                streaming=False,
                supported_protocol_bindings=["JSONRPC"],
            )
            client = ClientFactory(config).create(card)
            message = Message(
                message_id="message-1",
                role=Role.ROLE_USER,
                parts=[Part(text=jsonlib.dumps({"action": "renew"}))],
            )
            final_task = None
            request = SendMessageRequest(
                message=message,
                configuration=SendMessageConfiguration(
                    task_push_notification_config=TaskPushNotificationConfig(
                        id="callback-1",
                        url="https://callback.example.test/events",
                    )
                ),
            )
            async for response in client.send_message(request):
                if response.HasField("task"):
                    final_task = response.task
            assert final_task is not None
            assert final_task.status.state == TaskState.TASK_STATE_COMPLETED
            artifacts = final_task.artifacts or []
            assert artifacts
            texts = [part.text for part in artifacts[0].parts if part.text]
            assert texts and texts[0]
            stored = await client.get_task(GetTaskRequest(id=final_task.id))
            assert stored.id == final_task.id
            listed = await client.list_tasks(ListTasksRequest())
            assert [task.id for task in listed.tasks] == [final_task.id]

            config = TaskPushNotificationConfig(
                task_id=final_task.id,
                id="callback-1",
                url="https://callback.example.test/events",
            )
            created_config = await client.create_task_push_notification_config(config)
            assert created_config.id == "callback-1"
            fetched_config = await client.get_task_push_notification_config(
                GetTaskPushNotificationConfigRequest(task_id=final_task.id, id="callback-1")
            )
            assert fetched_config.url == config.url
            configs = await client.list_task_push_notification_configs(
                ListTaskPushNotificationConfigsRequest(task_id=final_task.id)
            )
            assert [item.id for item in configs.configs] == ["callback-1"]
            await client.delete_task_push_notification_config(
                DeleteTaskPushNotificationConfigRequest(
                    task_id=final_task.id,
                    id="callback-1",
                )
            )
            with pytest.raises(TaskNotFoundError):
                await client.get_task(GetTaskRequest(id="missing-task"))
            await agent.stop()
            await agent.shutdown()

    @pytest.mark.asyncio
    async def test_official_client_receives_streaming_artifact_chunks(self):
        """An official SDK client receives a streamed built-in-runtime result."""
        import json as jsonlib

        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import (
            Message,
            Part,
            Role,
            SendMessageConfiguration,
            SendMessageRequest,
            TaskState,
        )

        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=FakeModelProvider(
                    FakeModelConfig(response="hello", stream_chunks=["hel", "lo"])
                )
            )
        )
        agent = DefaultMicroAgent(_definition(), runtime)
        await agent.initialize()
        await agent.start()
        app = create_app(agent, base_url="http://test")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            resolver = A2ACardResolver(httpx_client=http_client, base_url="http://test")
            card = await resolver.get_agent_card()
            assert card.capabilities.streaming is True

            config = ClientConfig(
                httpx_client=http_client,
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
            )
            client = ClientFactory(config).create(card)
            message = Message(
                message_id="message-2",
                role=Role.ROLE_USER,
                parts=[Part(text=jsonlib.dumps({"action": "stream"}))],
            )
            updates = []
            final_task = None
            terminal_state = None
            streamed_artifacts = []
            request = SendMessageRequest(
                message=message,
                configuration=SendMessageConfiguration(),
            )
            async for response in client.send_message(request):
                if response.HasField("task"):
                    final_task = response.task
                if response.HasField("status_update"):
                    terminal_state = response.status_update.status.state
                if response.HasField("artifact_update"):
                    streamed_artifacts.append(response.artifact_update.artifact)
                updates.append(response)

            assert final_task is not None
            assert terminal_state == TaskState.TASK_STATE_COMPLETED
            assert streamed_artifacts
            streamed_text = "".join(
                part.text for artifact in streamed_artifacts for part in artifact.parts if part.text
            )
            assert streamed_text == "hello"
            assert updates
        await agent.stop()
        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_official_client_cancels_an_in_flight_task(self, tmp_path):
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import (
            CancelTaskRequest,
            Message,
            Part,
            Role,
            SendMessageConfiguration,
            SendMessageRequest,
            TaskState,
        )

        from micro_agent.models import FakeModelConfig, FakeModelProvider
        from runtimes.adk import AdkRuntime, AdkRuntimeConfig

        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingProvider(FakeModelProvider):
            async def generate(self, config, messages, tools=None):
                started.set()
                await release.wait()
                return await super().generate(config, messages, tools)

        runtime = AdkRuntime(
            AdkRuntimeConfig(
                model_provider=BlockingProvider(FakeModelConfig(response="never reached"))
            )
        )
        agent = DefaultMicroAgent(_definition(), runtime)
        await agent.initialize()
        await agent.start()
        app = create_app(
            agent,
            base_url="http://test",
            a2a_store_path=str(tmp_path / "a2a.db"),
            a2a_push_sender=self._NoopPushSender(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            card = await A2ACardResolver(
                httpx_client=http_client, base_url="http://test"
            ).get_agent_card()
            client = ClientFactory(
                ClientConfig(
                    httpx_client=http_client,
                    streaming=False,
                    supported_protocol_bindings=["JSONRPC"],
                )
            ).create(card)
            request = SendMessageRequest(
                message=Message(
                    message_id="message-cancel",
                    role=Role.ROLE_USER,
                    parts=[Part(text='{"action": "block"}')],
                ),
                configuration=SendMessageConfiguration(return_immediately=True),
            )
            initial = None
            async for response in client.send_message(request):
                if response.HasField("task"):
                    initial = response.task
                    break
            assert initial is not None
            await asyncio.wait_for(started.wait(), timeout=1)
            canceled = await client.cancel_task(CancelTaskRequest(id=initial.id))
            assert canceled.id == initial.id
            assert canceled.status.state == TaskState.TASK_STATE_CANCELED
            release.set()
        await agent.stop()
        await agent.shutdown()
