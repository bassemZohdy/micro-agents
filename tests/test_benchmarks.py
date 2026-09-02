"""Tests for deterministic benchmark execution and budget enforcement."""

from __future__ import annotations

import pytest

from benchmarks.run_benchmark import (
    _execute_load,
    evaluate_budget,
    run_scenario,
)


def test_evaluate_budget_reports_all_violations() -> None:
    metrics = {
        "error_rate": 0.1,
        "p95_ms": 12.0,
        "throughput_rps": 3.0,
        "peak_traced_memory_mb": 20.0,
    }
    budget = {
        "max_error_rate": 0.0,
        "max_p95_ms": 10.0,
        "min_throughput_rps": 5.0,
        "max_peak_traced_memory_mb": 16.0,
    }

    violations = evaluate_budget(metrics, budget)

    assert len(violations) == 4
    assert all("must be" in violation for violation in violations)


@pytest.mark.asyncio
async def test_execute_load_records_failed_operations() -> None:
    async def operation(index: int) -> bool:
        return index != 1

    metrics = await _execute_load(operation, iterations=3, concurrency=2)

    assert metrics["iterations"] == 3
    assert metrics["concurrency"] == 2
    assert metrics["errors"] == 1
    assert metrics["error_rate"] == pytest.approx(1 / 3)
    assert metrics["p95_ms"] >= metrics["p50_ms"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["runtime_fake", "http_fake"])
async def test_fake_scenarios_run_and_pass_small_budgeted_load(scenario: str) -> None:
    result = await run_scenario(scenario, iterations=2, concurrency=1)

    assert result["scenario"] == scenario
    assert result["metrics"]["errors"] == 0
    assert result["passed"] is True
    assert result["violations"] == []


@pytest.mark.asyncio
async def test_run_scenario_rejects_non_positive_overrides() -> None:
    with pytest.raises(ValueError, match="iterations"):
        await run_scenario("runtime_fake", iterations=0)
    with pytest.raises(ValueError, match="concurrency"):
        await run_scenario("runtime_fake", concurrency=0)
