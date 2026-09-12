"""Run a rising-concurrency capacity matrix against deployed endpoints.

The matrix is sequential by level so the report can show how latency, errors,
and throughput change as load rises. It is operator-invoked and does not claim
that a single endpoint proves multi-replica behavior; run it against the
production front door with shared state enabled when establishing capacity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

try:
    from .run_external_benchmark import run_http_load, run_mcp_load
except ImportError:  # pragma: no cover - direct script execution
    from run_external_benchmark import run_http_load, run_mcp_load

Metrics = dict[str, float | int]
StageRunner = Callable[[int, int], Awaitable[Metrics]]


def parse_concurrency_levels(raw: str) -> list[int]:
    """Parse a strictly increasing, comma-separated concurrency matrix."""
    try:
        levels = [int(value.strip()) for value in raw.split(",") if value.strip()]
    except ValueError as exc:
        raise ValueError("concurrency levels must be comma-separated integers") from exc
    if not levels or any(level < 1 for level in levels):
        raise ValueError("concurrency levels must be greater than zero")
    if levels != sorted(set(levels)):
        raise ValueError("concurrency levels must be strictly increasing")
    return levels


async def run_capacity_matrix(
    runner: StageRunner,
    *,
    iterations_per_level: int,
    concurrency_levels: list[int],
) -> dict[str, Any]:
    """Run each load level in order and summarize capacity signals."""
    if iterations_per_level < 1:
        raise ValueError("iterations_per_level must be greater than zero")
    if (
        not concurrency_levels
        or concurrency_levels != sorted(set(concurrency_levels))
        or any(level < 1 for level in concurrency_levels)
    ):
        raise ValueError("concurrency levels must be strictly increasing and positive")

    stages: list[dict[str, Any]] = []
    for concurrency in concurrency_levels:
        metrics = await runner(iterations_per_level, concurrency)
        stages.append({"concurrency": concurrency, "metrics": metrics})

    def number(stage: dict[str, Any], key: str) -> float:
        return float(stage["metrics"][key])

    peak_throughput_stage = max(stages, key=lambda stage: number(stage, "throughput_rps"))
    return {
        "iterations_per_level": iterations_per_level,
        "concurrency_levels": concurrency_levels,
        "stages": stages,
        "summary": {
            "total_iterations": sum(int(stage["metrics"]["iterations"]) for stage in stages),
            "total_errors": sum(int(stage["metrics"]["errors"]) for stage in stages),
            "max_error_rate": max(number(stage, "error_rate") for stage in stages),
            "max_p95_ms": max(number(stage, "p95_ms") for stage in stages),
            "peak_throughput_rps": number(peak_throughput_stage, "throughput_rps"),
            "peak_throughput_concurrency": peak_throughput_stage["concurrency"],
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", choices=("http", "mcp"), required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--concurrency-levels", default="1,2,4,8")
    parser.add_argument("--iterations-per-level", type=int, default=50)
    parser.add_argument(
        "--bearer-token-env",
        help="Environment variable containing the bearer token; never pass tokens as arguments.",
    )
    parser.add_argument("--tool", help="MCP tool name (required with --protocol mcp)")
    parser.add_argument("--arguments", default="{}", help="JSON object of MCP tool arguments")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--deployment-label")
    parser.add_argument("--replicas", type=int)
    parser.add_argument("--shared-state", choices=("redis", "postgres", "redis+postgres"))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help="Report errors without returning a failure exit code.",
    )
    return parser.parse_args()


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--arguments must be valid JSON") from exc
    if not isinstance(arguments, dict):
        raise ValueError("--arguments must contain a JSON object")
    return arguments


def main() -> int:
    args = _parse_args()
    try:
        levels = parse_concurrency_levels(args.concurrency_levels)
        arguments = _parse_arguments(args.arguments)
        if args.protocol == "mcp" and not args.tool:
            raise ValueError("--tool is required with --protocol mcp")
        if args.replicas is not None and args.replicas < 1:
            raise ValueError("replicas must be greater than zero")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    token = os.environ.get(args.bearer_token_env) if args.bearer_token_env else None

    async def runner(iterations: int, concurrency: int) -> Metrics:
        if args.protocol == "http":
            return await run_http_load(
                args.endpoint,
                iterations=iterations,
                concurrency=concurrency,
                bearer_token=token,
            )
        assert args.tool is not None
        return await run_mcp_load(
            args.endpoint,
            tool=args.tool,
            arguments=arguments,
            iterations=iterations,
            concurrency=concurrency,
            bearer_token=token,
            timeout_seconds=args.timeout_seconds,
        )

    try:
        matrix = asyncio.run(
            run_capacity_matrix(
                runner,
                iterations_per_level=args.iterations_per_level,
                concurrency_levels=levels,
            )
        )
    except (ConnectionError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    deployment: dict[str, str | int] = {}
    if args.deployment_label is not None:
        deployment["label"] = args.deployment_label
    if args.replicas is not None:
        deployment["replicas"] = args.replicas
    if args.shared_state is not None:
        deployment["shared_state"] = args.shared_state
    result: dict[str, Any] = {
        "protocol": args.protocol,
        "endpoint": args.endpoint,
        "deployment": deployment,
        "matrix": matrix,
        "passed": matrix["summary"]["total_errors"] == 0,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] or args.no_enforce else 1


if __name__ == "__main__":
    sys.exit(main())
