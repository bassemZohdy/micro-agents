"""Tests for durable A2A task and push-notification storage."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from micro_agent.interoperability.a2a_store import (
    HttpxPushNotificationSender,
    RedisA2ATaskStore,
    RedisPushNotificationConfigStore,
    SqliteA2ATaskStore,
    SqlitePushNotificationConfigStore,
)

pytest.importorskip("a2a")
from a2a.types import (
    AuthenticationInfo,
    ListTasksRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)

from tests.fake_redis import FakeRedis, FakeRedisBackend


def _task(task_id: str = "task-1") -> object:
    return Task(
        id=task_id,
        context_id="context-1",
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
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
    assert (await reopened.get("task-1", tenant_a)).status.state == TaskState.TASK_STATE_WORKING
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
async def test_redis_a2a_stores_share_tenant_scoped_tasks_and_push_configs() -> None:
    backend = FakeRedisBackend()
    endpoint = "redis://redis.example.test/0"
    task_a = RedisA2ATaskStore(endpoint, namespace="a2a-test", client=FakeRedis(backend))
    task_b = RedisA2ATaskStore(endpoint, namespace="a2a-test", client=FakeRedis(backend))
    push_a = RedisPushNotificationConfigStore(
        endpoint, namespace="a2a-test", client=FakeRedis(backend)
    )
    push_b = RedisPushNotificationConfigStore(
        endpoint, namespace="a2a-test", client=FakeRedis(backend)
    )
    tenant_a = SimpleNamespace(state={"tenant_id": "tenant-a"})
    tenant_b = SimpleNamespace(state={"tenant_id": "tenant-b"})
    config = TaskPushNotificationConfig(id="callback-1", url="https://callback.example.test/events")
    try:
        await task_a.save(_task(), tenant_a)
        shared = await task_b.get("task-1", tenant_a)
        assert shared is not None
        assert shared.id == "task-1"
        listed = await task_b.list(ListTasksRequest(), tenant_a)
        assert [item.id for item in listed.tasks] == ["task-1"]
        assert await task_b.get("task-1", tenant_b) is None
        await push_a.set_info("task-1", config)
        assert (await push_b.get_info("task-1"))[0].id == "callback-1"
        await push_b.delete_info("task-1", "callback-1")
        assert await push_a.get_info("task-1") == []
        assert await task_a.health_check()
        assert await push_a.health_check()
    finally:
        await task_a.delete("task-1", tenant_a)
        await push_a.delete_info("task-1")
        await task_a.aclose()
        await task_b.aclose()
        await push_a.aclose()
        await push_b.aclose()


@pytest.mark.asyncio
async def test_push_config_store_round_trips_and_sender_authenticates(tmp_path) -> None:
    store = SqlitePushNotificationConfigStore(tmp_path / "push.db")
    await store.set_info(
        "task-1",
        TaskPushNotificationConfig(
            id="callback-1",
            url="https://callback.example.test/events",
            token="callback-token",
            authentication=AuthenticationInfo(scheme="bearer", credentials="secret"),
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
    assert json.loads(received[0].content)["task"]["id"] == "task-1"
    await sender.aclose()
    await store.close()


def test_push_sender_rejects_non_local_http(tmp_path) -> None:
    store = SqlitePushNotificationConfigStore(tmp_path / "push.db")
    sender = HttpxPushNotificationSender(store, client=httpx.AsyncClient())
    with pytest.raises(ValueError, match="HTTPS"):
        sender._validate_url("http://callback.example.test/events")
