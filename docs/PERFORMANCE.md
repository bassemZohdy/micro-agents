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

## External deployment harness

Use [`benchmarks/run_external_benchmark.py`](https://github.com/bassemZohdy/micro-agents/blob/main/benchmarks/run_external_benchmark.py)
when a deployed endpoint is available. It supports bounded concurrent calls to
the Micro-Agent HTTPS API and to an MCP Streamable HTTP server through the
official MCP SDK:

```bash
python benchmarks/run_external_benchmark.py \
  --protocol http \
  --endpoint https://agent.example.com/v1/invoke \
  --iterations 100 --concurrency 10

python benchmarks/run_external_benchmark.py \
  --protocol mcp \
  --endpoint https://mcp.example.com/mcp \
  --tool search \
  --arguments '{"query":"benchmark"}' \
  --bearer-token-env MCP_BENCHMARK_TOKEN \
  --iterations 50 --concurrency 5
```

Remote endpoints must use HTTPS. Put bearer tokens in an environment
variable; tokens are not accepted as command-line arguments and are not
included in the JSON report. Live model, network, tool, replica, and datastore
measurements depend on the deployment. Record the endpoint version, replica
count, datastore topology, load, and report when establishing production SLOs.

For distributed contention and capacity planning, run a sequential matrix of
increasing concurrency levels through the deployed front door:

```bash
python benchmarks/run_capacity_benchmark.py \
  --protocol http \
  --endpoint https://gateway.example.com/v1/invoke \
  --concurrency-levels 1,2,4,8,16 \
  --iterations-per-level 100 \
  --deployment-label prod-2026-09-12 \
  --replicas 3 \
  --shared-state redis
```

The report preserves every stage and summarizes total errors, maximum error
rate, maximum p95 latency, and peak throughput. A passing report only means
the calls completed; it is not a universal capacity budget. Run repeated
matrices during a controlled window against the actual multi-replica service
and shared Redis/Postgres topology, then review saturation, telemetry, and
rollback behavior with the deployment owner.

## CI policy

CI runs both fake scenarios with budget enforcement after the unit tests. A
budget failure blocks the workflow and should be investigated alongside the
benchmark report. The external harness is operator-invoked and is not run
against deployment endpoints by CI. Distributed capacity work should repeat
the harness at rising concurrency against shared Redis/Postgres services and
review p95 latency, saturation, and telemetry together. Changes to thresholds
must be reviewed with the benchmark methodology and documented in the
changelog.
