"""Tests for the operator-invoked capacity matrix."""

from __future__ import annotations

import pytest

from benchmarks.run_capacity_benchmark import parse_concurrency_levels, run_capacity_matrix


def test_parse_concurrency_levels_requires_rising_positive_values() -> None:
    assert parse_concurrency_levels("1, 2, 8") == [1, 2, 8]
    with pytest.raises(ValueError, match="strictly increasing"):
        parse_concurrency_levels("1,4,2")
    with pytest.raises(ValueError, match="greater than zero"):
        parse_concurrency_levels("0,2")


@pytest.mark.asyncio
async def test_capacity_matrix_runs_stages_sequentially_and_summarizes() -> None:
    calls: list[tuple[int, int]] = []

    async def runner(iterations: int, concurrency: int) -> dict[str, float | int]:
        calls.append((iterations, concurrency))
        return {
            "iterations": iterations,
            "concurrency": concurrency,
            "errors": 0,
            "error_rate": 0.0,
            "throughput_rps": float(concurrency),
            "p95_ms": float(concurrency * 2),
        }

    result = await run_capacity_matrix(
        runner,
        iterations_per_level=5,
        concurrency_levels=[1, 2, 4],
    )

    assert calls == [(5, 1), (5, 2), (5, 4)]
    assert result["summary"] == {
        "total_iterations": 15,
        "total_errors": 0,
        "max_error_rate": 0.0,
        "max_p95_ms": 8.0,
        "peak_throughput_rps": 4.0,
        "peak_throughput_concurrency": 4,
    }
