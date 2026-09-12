"""Tests for the opt-in external benchmark harness."""

from __future__ import annotations

import httpx
import pytest

from benchmarks.run_external_benchmark import run_http_load, validate_endpoint


def test_validate_endpoint_requires_https_for_remote_targets() -> None:
    assert validate_endpoint("http://localhost:8000/v1/invoke")
    assert validate_endpoint("https://agent.example.com/v1/invoke")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_endpoint("http://agent.example.com/v1/invoke")
    with pytest.raises(ValueError, match="userinfo"):
        validate_endpoint("https://user:secret@agent.example.com/v1/invoke")


@pytest.mark.asyncio
async def test_http_load_uses_bearer_header_and_bounded_requests() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "success"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        metrics = await run_http_load(
            "https://agent.example.com/v1/invoke",
            iterations=3,
            concurrency=2,
            bearer_token="test-token",
            client=client,
        )

    assert metrics["errors"] == 0
    assert len(requests) == 3
    assert all(request.headers["authorization"] == "Bearer test-token" for request in requests)
    assert all("benchmark_index" in request.content.decode() for request in requests)
