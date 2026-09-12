"""Opt-in load harness for deployed Micro-Agent and MCP HTTP endpoints.

This harness is intentionally operator-invoked. It measures a deployment and
never runs in deterministic CI, where the existing fake-provider scenarios
remain the regression guardrails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from micro_agent.mcp import McpConfig
from micro_agent.mcp.sdk_client import SdkMcpClient

try:
    from .run_benchmark import _execute_load
except ImportError:  # pragma: no cover - direct script execution
    from run_benchmark import _execute_load


def validate_endpoint(endpoint: str) -> str:
    """Validate an external endpoint without allowing credential-bearing URLs."""
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("endpoint must not contain userinfo or a fragment")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("remote endpoints must use HTTPS")
    return endpoint


async def run_http_load(
    endpoint: str,
    *,
    iterations: int,
    concurrency: int,
    bearer_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, float | int]:
    """POST benchmark requests to a deployed Micro-Agent HTTP endpoint."""
    validate_endpoint(endpoint)
    if iterations < 1 or concurrency < 1:
        raise ValueError("iterations and concurrency must be greater than zero")
    owned_client = client is None
    request_client = client or httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=60.0
    )
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None

    async def invoke(index: int) -> bool:
        response = await request_client.post(
            endpoint,
            headers=headers,
            json={
                "input": {"benchmark_index": index},
                "request_id": f"external-http-{index}",
            },
        )
        return response.is_success

    try:
        return await _execute_load(invoke, iterations=iterations, concurrency=concurrency)
    finally:
        if owned_client:
            await request_client.aclose()


async def run_mcp_load(
    endpoint: str,
    *,
    tool: str,
    arguments: dict[str, Any],
    iterations: int,
    concurrency: int,
    bearer_token: str | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, float | int]:
    """Call a discovered tool through the official Streamable HTTP client."""
    validate_endpoint(endpoint)
    if iterations < 1 or concurrency < 1:
        raise ValueError("iterations and concurrency must be greater than zero")
    if not tool:
        raise ValueError("tool must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    client = SdkMcpClient(
        call_timeout_seconds=timeout_seconds,
        reconnect_attempts=0,
    )
    config = McpConfig(
        ref="external-benchmark",
        transport="streamable-http",
        endpoint=endpoint,
        timeout_seconds=max(1, int(timeout_seconds)),
    )
    await client.connect(config, credential=bearer_token)
    try:
        discovery = await client.discover()
        if tool not in {item.name for item in discovery.tools}:
            raise ValueError(f"tool '{tool}' was not advertised by the MCP server")

        async def invoke(index: int) -> bool:
            payload = dict(arguments)
            payload["benchmark_index"] = index
            await client.call_tool(tool, payload)
            return True

        return await _execute_load(invoke, iterations=iterations, concurrency=concurrency)
    finally:
        await client.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("http", "mcp"), required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--bearer-token-env",
        help="Environment variable containing the bearer token; never pass tokens as arguments.",
    )
    parser.add_argument("--tool", help="MCP tool name (required with --protocol mcp)")
    parser.add_argument("--arguments", default="{}", help="JSON object of MCP tool arguments")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help="Report errors without returning a failure exit code.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    token = os.environ.get(args.bearer_token_env) if args.bearer_token_env else None
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as exc:
        raise ValueError("--arguments must be valid JSON") from exc
    if not isinstance(arguments, dict):
        raise ValueError("--arguments must contain a JSON object")
    if args.protocol == "mcp" and not args.tool:
        raise ValueError("--tool is required with --protocol mcp")

    if args.protocol == "http":
        metrics = asyncio.run(
            run_http_load(
                args.endpoint,
                iterations=args.iterations,
                concurrency=args.concurrency,
                bearer_token=token,
            )
        )
    else:
        assert args.tool is not None
        metrics = asyncio.run(
            run_mcp_load(
                args.endpoint,
                tool=args.tool,
                arguments=arguments,
                iterations=args.iterations,
                concurrency=args.concurrency,
                bearer_token=token,
                timeout_seconds=args.timeout_seconds,
            )
        )

    result = {
        "protocol": args.protocol,
        "endpoint": args.endpoint,
        "iterations": args.iterations,
        "concurrency": args.concurrency,
        "metrics": metrics,
        "passed": metrics["errors"] == 0,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] or args.no_enforce else 1


if __name__ == "__main__":
    sys.exit(main())
