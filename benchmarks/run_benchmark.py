"""Deterministic framework performance benchmark and budget evaluator."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import sys
import time
import tracemalloc
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import httpx

from micro_agent.core import AgentRequest, DefaultMicroAgent
from micro_agent.definition import load_definition_from_dict
from micro_agent.interoperability import create_app
from micro_agent.models import FakeModelConfig
from runtimes.adk import AdkRuntime, AdkRuntimeConfig

BUDGET_PATH = Path(__file__).with_name("budgets.json")


def load_budgets() -> dict[str, Any]:
    """Load versioned benchmark budgets from the repository."""
    return cast(dict[str, Any], json.loads(BUDGET_PATH.read_text(encoding="utf-8")))


def _definition() -> Any:
    return load_definition_from_dict(
        {
            "apiVersion": "microagents.io/v1alpha1",
            "kind": "MicroAgent",
            "metadata": {"name": "benchmark-agent", "version": "1.0.0"},
            "spec": {
                "behavior": {"instructions": "Return the deterministic benchmark answer."},
                "dependencies": {"model": {"ref": "fake-model"}},
            },
        }
    )


async def _build_agent() -> DefaultMicroAgent:
    runtime = AdkRuntime(
        AdkRuntimeConfig(fake_model_config=FakeModelConfig(response="benchmark response"))
    )
    agent = DefaultMicroAgent(_definition(), runtime)
    await agent.initialize()
    await agent.start()
    return agent


async def _close_agent(agent: DefaultMicroAgent) -> None:
    await agent.stop()
    await agent.shutdown()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


async def _execute_load(
    operation: Callable[[int], Awaitable[bool]],
    *,
    iterations: int,
    concurrency: int,
) -> dict[str, float | int]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies_ms: list[float] = []
    errors = 0

    async def one(index: int) -> None:
        nonlocal errors
        async with semaphore:
            started = time.perf_counter()
            try:
                ok = await operation(index)
                if not ok:
                    errors += 1
            except Exception:  # noqa: BLE001 - benchmark records failures as metrics
                errors += 1
            finally:
                latencies_ms.append((time.perf_counter() - started) * 1000.0)

    tracemalloc.start()
    wall_started = time.perf_counter()
    await asyncio.gather(*(one(index) for index in range(iterations)))
    wall_seconds = max(time.perf_counter() - wall_started, 1e-9)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "iterations": iterations,
        "concurrency": concurrency,
        "errors": errors,
        "error_rate": errors / iterations,
        "wall_seconds": wall_seconds,
        "throughput_rps": iterations / wall_seconds,
        "p50_ms": _percentile(latencies_ms, 0.50),
        "p95_ms": _percentile(latencies_ms, 0.95),
        "max_ms": max(latencies_ms, default=0.0),
        "peak_traced_memory_mb": peak_bytes / (1024 * 1024),
    }


async def _runtime_scenario(iterations: int, concurrency: int) -> dict[str, float | int]:
    agent = await _build_agent()
    try:
        for index in range(5):
            await agent.invoke(
                AgentRequest(input={"warmup": index}, request_id=f"warmup-runtime-{index}")
            )

        async def invoke(index: int) -> bool:
            response = await agent.invoke(
                AgentRequest(input={"index": index}, request_id=f"runtime-bench-{index}")
            )
            return response.status == "success"

        return await _execute_load(
            invoke,
            iterations=iterations,
            concurrency=concurrency,
        )
    finally:
        await _close_agent(agent)


async def _http_scenario(iterations: int, concurrency: int) -> dict[str, float | int]:
    agent = await _build_agent()
    app = create_app(agent)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
            for index in range(5):
                response = await client.post(
                    "/v1/invoke",
                    json={
                        "input": {"warmup": index},
                        "request_id": f"warmup-http-{index}",
                    },
                )
                response.raise_for_status()

            async def invoke(index: int) -> bool:
                response = await client.post(
                    "/v1/invoke",
                    json={"input": {"index": index}, "request_id": f"http-bench-{index}"},
                )
                return response.status_code == 200

            return await _execute_load(
                invoke,
                iterations=iterations,
                concurrency=concurrency,
            )
    finally:
        await _close_agent(agent)


def evaluate_budget(metrics: dict[str, float | int], budget: dict[str, Any]) -> list[str]:
    """Return deterministic budget violations for one benchmark result."""
    checks = (
        ("error_rate", "max_error_rate", "max"),
        ("p95_ms", "max_p95_ms", "max"),
        ("throughput_rps", "min_throughput_rps", "min"),
        ("peak_traced_memory_mb", "max_peak_traced_memory_mb", "max"),
    )
    violations: list[str] = []
    for metric_name, budget_name, direction in checks:
        actual = float(metrics[metric_name])
        limit = float(budget[budget_name])
        failed = actual > limit if direction == "max" else actual < limit
        if failed:
            comparator = "<=" if direction == "max" else ">="
            violations.append(f"{metric_name}={actual:.3f} must be {comparator} {limit:.3f}")
    return violations


async def run_scenario(
    scenario: str,
    *,
    iterations: int | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """Execute one named benchmark and evaluate its configured budget."""
    budgets = load_budgets()
    budget = budgets["scenarios"][scenario]
    effective_iterations = int(budget["iterations"]) if iterations is None else iterations
    effective_concurrency = int(budget["concurrency"]) if concurrency is None else concurrency
    if effective_iterations < 1:
        raise ValueError("iterations must be greater than zero")
    if effective_concurrency < 1:
        raise ValueError("concurrency must be greater than zero")

    if scenario == "runtime_fake":
        metrics = await _runtime_scenario(effective_iterations, effective_concurrency)
    elif scenario == "http_fake":
        metrics = await _http_scenario(effective_iterations, effective_concurrency)
    else:
        raise ValueError(f"unknown benchmark scenario: {scenario}")

    violations = evaluate_budget(metrics, budget)
    return {
        "scenario": scenario,
        "budget_version": budgets["version"],
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.system(),
            "machine": platform.machine(),
        },
        "metrics": metrics,
        "budget": budget,
        "passed": not violations,
        "violations": violations,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("runtime_fake", "http_fake"),
        default="runtime_fake",
    )
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--no-enforce",
        action="store_true",
        help="Report budget violations without returning a failure exit code.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = asyncio.run(
        run_scenario(
            args.scenario,
            iterations=args.iterations,
            concurrency=args.concurrency,
        )
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    if result["violations"] and not args.no_enforce:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
