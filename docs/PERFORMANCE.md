# Performance and resource budgets

The repository includes deterministic smoke benchmarks for framework overhead.
They use the fake model and an in-process ASGI transport, so they do not
measure provider latency, network performance, or production model capacity.
The budgets are CI guardrails for regressions in the reference implementation,
not production service-level objectives.

## Scenarios

| Scenario | Path exercised | Default load |
|---|---|---:|
| `runtime_fake` | Direct `DefaultMicroAgent.invoke()` calls | 200 requests / 20 concurrent |
| `http_fake` | `POST /v1/invoke` through the FastAPI app | 100 requests / 10 concurrent |

Each run reports error rate, p50/p95/max latency, throughput, and peak
`tracemalloc` memory. The versioned thresholds live in
[`benchmarks/budgets.json`](https://github.com/bassemZohdy/micro-agents/blob/main/benchmarks/budgets.json).

## Run locally

Install the development dependencies, then run either scenario:

```bash
python benchmarks/run_benchmark.py --scenario runtime_fake
python benchmarks/run_benchmark.py --scenario http_fake
```

The command exits non-zero when a budget is exceeded. Use smaller loads while
iterating, or use `--no-enforce` to inspect measurements without failing:

```bash
python benchmarks/run_benchmark.py \
  --scenario runtime_fake --iterations 20 --concurrency 4 --no-enforce
```

The benchmark prints stable JSON suitable for CI artifacts; `--json-out`
writes the same report to a file. Run-to-run values naturally vary with the
Python version, operating system, and shared-runner load. Keep thresholds
generous enough to catch material regressions without pretending to be
production SLOs.

## CI policy

CI runs both fake scenarios with budget enforcement after the unit tests. A
budget failure blocks the workflow and should be investigated alongside the
benchmark report. Changes to thresholds must be reviewed with the benchmark
methodology and documented in the changelog.
