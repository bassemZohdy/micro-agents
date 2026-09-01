"""Small async Redis test double shared by provider unit tests."""

from __future__ import annotations

import asyncio


class WatchError(RuntimeError):
    """Redis WATCH conflict raised by the test double."""


class FakeRedisBackend:
    """State shared by independent fake Redis clients."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.index: dict[str, dict[str, float]] = {}


class FakeRedisPipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._commands: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._watched: dict[str, str | None] = {}

    async def watch(self, *keys: str) -> None:
        for key in keys:
            self._client._purge(key)
            self._watched[key] = self._client.backend.values.get(key)

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    def multi(self) -> FakeRedisPipeline:
        return self

    async def reset(self) -> None:
        self._watched.clear()
        self._commands.clear()

    def __getattr__(self, name: str):
        def enqueue(*args: object, **kwargs: object) -> FakeRedisPipeline:
            self._commands.append((name, args, kwargs))
            return self

        return enqueue

    async def execute(self) -> list[object]:
        for key, expected in self._watched.items():
            self._client._purge(key)
            if self._client.backend.values.get(key) != expected:
                self._watched.clear()
                self._commands.clear()
                raise WatchError("watched key changed")
        results: list[object] = []
        for name, args, kwargs in self._commands:
            result = getattr(self._client, name)(*args, **kwargs)
            results.append(await result)
        return results


class FakeRedis:
    def __init__(self, backend: FakeRedisBackend) -> None:
        self.backend = backend
        self.closed = False

    def _purge(self, key: str) -> None:
        if self.backend.expiry.get(key, float("inf")) <= asyncio.get_running_loop().time():
            self.backend.values.pop(key, None)
            self.backend.expiry.pop(key, None)

    def pipeline(self, *, transaction: bool = False) -> FakeRedisPipeline:
        assert transaction is True
        return FakeRedisPipeline(self)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        self._purge(key)
        if nx and key in self.backend.values:
            return False
        self.backend.values[key] = value
        if ex is not None:
            self.backend.expiry[key] = asyncio.get_running_loop().time() + ex
        else:
            self.backend.expiry.pop(key, None)
        return True

    async def get(self, key: str) -> str | None:
        self._purge(key)
        return self.backend.values.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [await self.get(key) for key in keys]

    async def delete(self, key: str) -> int:
        existed = key in self.backend.values
        self.backend.values.pop(key, None)
        self.backend.expiry.pop(key, None)
        return int(existed)

    async def zadd(self, index: str, values: dict[str, float]) -> int:
        members = self.backend.index.setdefault(index, {})
        added = 0
        for key, score in values.items():
            if key not in members:
                added += 1
            members[key] = score
        return added

    async def zrem(self, index: str, key: str) -> int:
        members = self.backend.index.get(index, {})
        removed = members.pop(key, None) is not None
        if not members:
            self.backend.index.pop(index, None)
        return int(removed)

    async def zrange(self, index: str, start: int, stop: int) -> list[str]:
        members = self.backend.index.get(index, {})
        ordered = [key for key, _score in sorted(members.items(), key=lambda item: item[1])]
        if stop == -1:
            return ordered[start:]
        return ordered[start : stop + 1]

    async def zcard(self, index: str) -> int:
        return len(self.backend.index.get(index, {}))

    async def zpopmin(self, index: str, *, count: int = 1) -> list[tuple[str, float]]:
        members = self.backend.index.get(index, {})
        ordered = sorted(members.items(), key=lambda item: item[1])[:count]
        for key, _score in ordered:
            members.pop(key, None)
        if not members:
            self.backend.index.pop(index, None)
        return ordered

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True
