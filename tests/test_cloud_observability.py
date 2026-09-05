"""Cloud C4 tests: trace, topology, cost, and audit aggregation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cloud.observability import InMemoryObservabilityStore, create_observability_app


def _span(trace_id: str, agent: str, span_id: str, **extra: object) -> dict[str, object]:
    return {"kind": "span", "trace_id": trace_id, "agent": agent, "span_id": span_id, **extra}


class TestStore:
    async def test_ingest_rejects_invalid_events(self):
        store = InMemoryObservabilityStore()
        with pytest.raises(ValueError, match="event kind"):
            await store.ingest([{"kind": "nope", "trace_id": "t", "agent": "a"}])
        with pytest.raises(ValueError, match="missing fields"):
            await store.ingest([{"kind": "span", "agent": "a"}])
        with pytest.raises(ValueError, match="exceeds"):
            await store.ingest([{"kind": "span", "trace_id": "t", "agent": "a"}] * 1001)

    async def test_traces_group_spans_across_agents(self):
        store = InMemoryObservabilityStore()
        await store.ingest(
            [
                _span("t1", "orchestrator", "s1", name="invoke"),
                _span("t1", "greeter", "s2", parent_span_id="s1", caller_agent="orchestrator"),
            ]
        )
        spans = await store.trace("t1")
        assert [span.agent for span in spans] == ["orchestrator", "greeter"]
        assert spans[1].caller_agent == "orchestrator"
        assert await store.trace("unknown") == []

    async def test_topology_counts_caller_edges(self):
        store = InMemoryObservabilityStore()
        await store.ingest(
            [
                _span("t1", "orchestrator", "s1"),
                _span("t1", "greeter", "s2", caller_agent="orchestrator"),
                _span("t2", "greeter", "s3", caller_agent="orchestrator"),
                _span("t3", "greeter", "s4"),  # self-entry: no edge
            ]
        )
        edges = await store.topology()
        assert edges == [{"caller_agent": "orchestrator", "callee_agent": "greeter", "calls": 2}]

    async def test_costs_roll_up_per_agent_and_tenant(self):
        store = InMemoryObservabilityStore()
        await store.ingest(
            [
                {
                    "kind": "usage",
                    "trace_id": "t1",
                    "agent": "greeter",
                    "tenant": "acme",
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cost_usd": 0.5,
                },
                {
                    "kind": "usage",
                    "trace_id": "t2",
                    "agent": "greeter",
                    "tenant": "other",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cost_usd": 0.25,
                },
            ]
        )
        acme = await store.costs(tenant="acme")
        assert acme["totals"] == {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.5}
        everything = await store.costs()
        assert everything["totals"]["input_tokens"] == 101
        assert everything["by_agent"]["greeter"]["cost_usd"] == 0.75
        assert (await store.costs(agent="ghost"))["totals"]["input_tokens"] == 0

    async def test_audit_is_append_only_and_tenant_filtered(self):
        store = InMemoryObservabilityStore()
        await store.ingest(
            [
                {
                    "kind": "audit",
                    "trace_id": "t1",
                    "agent": "greeter",
                    "tenant": "acme",
                    "action": "tool.clock",
                    "decision": "allowed",
                },
                {
                    "kind": "audit",
                    "trace_id": "t2",
                    "agent": "greeter",
                    "tenant": "other",
                    "action": "tool.write",
                    "decision": "denied",
                },
            ]
        )
        all_events = await store.audit_events()
        assert [event["decision"] for event in all_events] == ["denied", "allowed"]
        acme = await store.audit_events(tenant="acme")
        assert [event["action"] for event in acme] == ["tool.clock"]
        assert len(await store.audit_events(limit=1)) == 1


class TestHttp:
    def test_http_ingest_and_views(self):
        with TestClient(create_observability_app()) as http:
            batch = {
                "events": [
                    _span("t1", "orchestrator", "s1"),
                    _span("t1", "greeter", "s2", caller_agent="orchestrator"),
                    {
                        "kind": "usage",
                        "trace_id": "t1",
                        "agent": "greeter",
                        "input_tokens": 5,
                        "output_tokens": 5,
                        "cost_usd": 0.1,
                    },
                    {
                        "kind": "audit",
                        "trace_id": "t1",
                        "agent": "greeter",
                        "action": "invoke",
                        "decision": "allowed",
                    },
                ]
            }
            assert http.post("/observability/events", json=batch).json() == {"accepted": 4}
            assert (
                http.post("/observability/events", json={"events": [{"kind": "x"}]}).status_code
                == 422
            )

            trace = http.get("/observability/traces/t1").json()
            assert [span["agent"] for span in trace["spans"]] == ["orchestrator", "greeter"]
            assert http.get("/observability/traces/unknown").status_code == 404

            edges = http.get("/observability/topology").json()["edges"]
            assert edges[0]["calls"] == 1

            costs = http.get("/observability/costs").json()
            assert costs["totals"]["cost_usd"] == pytest.approx(0.1)

            audit = http.get("/observability/audit").json()["events"]
            assert audit[0]["action"] == "invoke"


class TestHttpHardening:
    """Batch ingest is atomic and validated; audit limits hold at the boundary."""

    def _client(self) -> TestClient:
        return TestClient(create_observability_app())

    def test_malformed_batch_is_atomic_422_without_partial_writes(self):
        with self._client() as http:
            batch = {
                "events": [
                    {"kind": "audit", "trace_id": "t1", "agent": "a", "action": "allow"},
                    "not-an-object",
                    {"kind": "span", "trace_id": "t2", "agent": "a"},
                ]
            }
            response = http.post("/observability/events", json=batch)
            assert response.status_code == 422
            # Nothing from the batch leaked into the store.
            assert http.get("/observability/audit").json()["events"] == []
            assert http.get("/observability/traces/t2").status_code == 404

    def test_non_dict_event_returns_422_not_500(self):
        with self._client() as http:
            response = http.post("/observability/events", json={"events": ["oops"]})
            assert response.status_code == 422

    def test_audit_limit_validated_at_http_boundary(self):
        with self._client() as http:
            http.post(
                "/observability/events",
                json={
                    "events": [
                        {"kind": "audit", "trace_id": f"t{i}", "agent": "a", "action": "allow"}
                        for i in range(3)
                    ]
                },
            )
            for bad_limit in ("0", "-1", "1001"):
                assert (
                    http.get("/observability/audit", params={"limit": bad_limit}).status_code == 422
                )
            ok = http.get("/observability/audit", params={"limit": 2})
            assert ok.status_code == 200
            assert len(ok.json()["events"]) == 2
