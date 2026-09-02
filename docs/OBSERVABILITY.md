# Observability — Metrics, Dashboards, and Alerts

Micro-Agent exposes operational metrics on `GET /metrics` in Prometheus text
format (`version=0.0.4`), alongside structured JSON logs, an audit-event
stream, and the health endpoints (`/health/live`, `/health/ready`).

## Scraping

Point a Prometheus scraper at the service port:

```yaml
scrape_configs:
  - job_name: micro-agent
    metrics_path: /metrics
    static_configs:
      - targets: ["micro-agent.default.svc:8080"]
```

The built-in collector keeps a bounded in-memory series: counter points
(`*_total`) accumulate per label set, other points export their latest value.
This is scrape-ready and dependency-free. For production aggregation with
histograms and exemplars, configure a native OpenTelemetry exporter from the
optional `otel` extra instead — the metric names below are unchanged.

## Metric inventory

Every metric the runtime emits, with its labels. The `agent` label carries
the definition name; `route` and `method` identify HTTP endpoints; `tool`
names the tool.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | counter | `route`, `method` | Accepted HTTP invocations |
| `http_auth_failures_total` | counter | `route` | Requests rejected by transport authentication |
| `http_rate_limit_failures_total` | counter | `route` | Rate-limiter internal failures |
| `http_rate_limit_rejections_total` | counter | `route` | Invocations rejected by the rate limiter |
| `http_streaming_rejections_total` | counter | `route` | Streaming requests refused by a runtime without streaming |
| `http_streaming_errors_total` | counter | `route` | Streaming invocations that fail after the SSE response starts |
| `agent_invocations_total` | counter | `agent` | Completed agent invocations |
| `agent_invocation_errors_total` | counter | `agent` | Failed agent invocations (deadline, provider, runtime) |
| `agent_invocation_latency_ms` | gauge | `agent` | Latest end-to-end invocation latency |
| `agent_retries_total` | counter | `agent` | Retried invocations under the configured error policy |
| `agent_retries_suppressed_total` | counter | `agent` | Retries suppressed after an unknown side-effect outcome |
| `model_latency_ms` | gauge | `agent` | Latest provider round-trip latency |
| `model_tokens_total` | counter | `agent` | Tokens consumed (usage reported by the provider) |
| `tool_calls_total` | counter | `agent`, `tool` | Tool executions |
| `tool_latency_ms` | gauge | `agent`, `tool` | Latest tool execution latency |
| `policy_denials_total` | counter | `agent`, `tool` | Deterministic policy denials (tool, side effect) |
| `circuit_breaker_trips_total` | counter | `agent` | Circuit breaker transitions to open after consecutive failures |
| `approval_requests_total` | counter | `tool` | Tool executions paused awaiting an approval decision |
| `operation_record_errors_total` | counter | `agent`, `tool` | Idempotency-store write failures (outcome unknown — inspect, do not blindly retry) |
| `http_request_latency_ms` | gauge | `route`, `method` | Latest HTTP request latency |
| `model_cost_usd_total` | counter | `agent`, `currency` | Estimated model cost, priced from configured per-1k-token rates |

Audit events (policy denials, approval decisions, authentication failures)
are also written as redacted JSON lines to the configured audit sink — use
them for per-incident investigation; use the counters above for alerting.

## Recommended dashboard

Panels that map directly onto the emitted series:

1. **Traffic** — `rate(http_requests_total[5m])` by `route`; invocation rate
   `rate(agent_invocations_total[5m])` by `agent`.
2. **Errors** — invocation error ratio
   `rate(agent_invocation_errors_total[5m]) / clamp_min(rate(agent_invocations_total[5m]), 1e-9)`;
   HTTP 401/429 rejection rates from the `http_*_total` counters.
3. **Latency** — `agent_invocation_latency_ms` and `model_latency_ms` per
   `agent` (latest-value gauges; for percentile panels, feed a native
   OpenTelemetry histogram exporter instead).
4. **Tools** — `rate(tool_calls_total[5m])` by `tool`, `tool_latency_ms`,
   and `rate(policy_denials_total[5m])` (a spike usually means a policy or
   prompt-injection investigation, not a code defect).
5. **Retries** — `rate(agent_retries_total[5m])` alongside
   `rate(agent_retries_suppressed_total[5m])` (suppression near retries
   indicates unsafe side-effect replay pressure); any
   `operation_record_errors_total` increase demands investigation before
   retries.
6. **Approvals** — `rate(approval_requests_total[5m])` (human-in-the-loop
   pressure) alongside denials.
7. **Cost** — `increase(model_tokens_total[1h])` and
   `increase(model_cost_usd_total[1h])` by `agent` (cost is populated when
   the deployment configures per-1k-token rates).
8. **HTTP latency** — `http_request_latency_ms` per `route` (latest-value
   gauge).
9. **Saturation** — in-flight invocations from
   `rate(agent_invocations_total[5m])` against the definition's
   `max_concurrency`; readiness failures from `/health/ready`.

## Recommended alerts

Alert on symptoms users feel; keep thresholds environment-specific.

| Alert | Expression (PromQL) | Rationale |
|---|---|---|
| Invocation errors | `rate(agent_invocation_errors_total[5m]) > 0.1 * clamp_min(rate(agent_invocations_total[5m]), 1e-9)` for 10m | Sustained >10% failure ratio |
| No successful invocations | `rate(agent_invocations_total[10m]) == 0 and increase(http_requests_total[10m]) > 0` | Traffic is arriving but nothing completes |
| Latency regression | `agent_invocation_latency_ms > 30000` for 10m | Latest invocation latency beyond a bound (latest-value gauge; tighten per environment) |
| Auth failures spike | `rate(http_auth_failures_total[5m]) > 1` for 10m | Misconfigured callers or credential probing |
| Rate-limit rejections | `rate(http_rate_limit_rejections_total[5m]) > 0` for 15m | Callers exceeding policy — capacity or abuse signal |
| Policy denials spike | `rate(policy_denials_total[5m]) > 0.1 * clamp_min(rate(tool_calls_total[5m]), 1e-9)` | Model attempting denied operations repeatedly |
| Retry suppression | `increase(agent_retries_suppressed_total[10m]) > 0` | Side-effect outcomes unknown — manual inspection advised |
| Idempotency write failures | `increase(operation_record_errors_total[10m]) > 0` | Deduplication state cannot be persisted |
| Circuit trips | `increase(circuit_breaker_trips_total[10m]) > 0` | Repeated invocation failures — dependency likely down (calls fail fast with 503 while open) |
| HTTP latency regression | `http_request_latency_ms{route="/v1/invoke"} > 5000` for 10m | Latest request latency beyond a bound (latest-value gauge) |
| Scrape absence | `up == 0` for 3m | The service stopped exporting |

## OpenTelemetry deployments

With the optional `otel` extra, traces and context propagation use
OpenTelemetry while the in-memory collector continues to serve `/metrics`.
For production metrics aggregation (histograms, exemplars, remote
exporters), register a native `MetricReader` exporter — for example the
Prometheus exporter from
`opentelemetry-exporter-prometheus` — instead of scraping the built-in
series; keep alert rules on the same metric names.
