"""Tests for durable A2A task and push-notification storage."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from micro_agent.interoperability.a2a_store import (
    HttpxPushNotificationSender,
    SqliteA2ATaskStore,
    SqlitePushNotificationConfigStore,
)

pytest.importorskip("a2a")
from a2a.server.tasks.push_notification_config_store import PushNotificationConfig
from a2a.types import PushNotificationAuthenticationInfo, Task, TaskState, TaskStatus


def _task(task_id: str = "task-1") -> object:
    return Task(
        id=task_id,
        contextId="context-1",
        status=TaskStatus(state=TaskState.working),
        metadata={"tenant_id": "tenant-a"},
    )


@pytest.mark.asyncio
async def test_sqlite_task_store_persists_and_isolates_tenants(tmp_path) -> None:
    path = tmp_path / "a2a.db"
    store = SqliteA2ATaskStore(path, ttl_seconds=60)
    tenant_a = SimpleNamespace(state={"tenant_id": "tenant-a"})
    tenant_b = SimpleNamespace(state={"tenant_id": "tenant-b"})

    await store.save(_task(), tenant_a)
    reopened = SqliteA2ATaskStore(path, ttl_seconds=60)
    assert (await reopened.get("task-1", tenant_a)).status.state == TaskState.working
    assert await reopened.get("task-1", tenant_b) is None

    await reopened.delete("task-1", tenant_a)
    assert await store.get("task-1", tenant_a) is None
    await store.close()
    await reopened.close()


@pytest.mark.asyncio
async def test_sqlite_task_store_expires_rows(tmp_path) -> None:
    store = SqliteA2ATaskStore(tmp_path / "a2a.db", ttl_seconds=0.01)
    await store.save(_task())
    await __import__("asyncio").sleep(0.03)
    assert await store.get("task-1") is None
    assert await store.purge_expired() == 0
    await store.close()


@pytest.mark.asyncio
async def test_push_config_store_round_trips_and_sender_authenticates(tmp_path) -> None:
    store = SqlitePushNotificationConfigStore(tmp_path / "push.db")
    await store.set_info(
        "task-1",
        PushNotificationConfig(
            id="callback-1",
            url="https://callback.example.test/events",
            token="callback-token",
            authentication=PushNotificationAuthenticationInfo(
                schemes=["bearer"], credentials="secret"
            ),
        ),
    )
    received: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    sender = HttpxPushNotificationSender(
        store,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        backoff_seconds=0,
    )
    await sender.send_notification(_task())
    assert len(received) == 1
    assert received[0].headers["Authorization"] == "Bearer secret"
    assert received[0].headers["X-A2A-Notification-Token"] == "callback-token"
    assert json.loads(received[0].content)["id"] == "task-1"
    await sender.aclose()
    await store.close()


def test_push_sender_rejects_non_local_http(tmp_path) -> None:
    store = SqlitePushNotificationConfigStore(tmp_path / "push.db")
    sender = HttpxPushNotificationSender(store, client=httpx.AsyncClient())
    with pytest.raises(ValueError, match="HTTPS"):
        sender._validate_url("http://callback.example.test/events")
