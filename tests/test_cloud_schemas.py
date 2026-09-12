"""Contract-artifact and cloud-boundary schema tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloud.descriptors import AgentDescriptor
from cloud.schemas import (
    CLOUD_SCHEMA_VERSION,
    AgentDescriptorContract,
    ConfigRecordContract,
    ObservabilityBatchContract,
    generate_schemas,
    load_schema,
)


def test_checked_in_cloud_schemas_match_generated_contracts() -> None:
    generated = generate_schemas()
    assert set(generated) == {
        "cloud-descriptor-v1alpha1",
        "cloud-config-v1alpha1",
        "cloud-observability-v1alpha1",
    }
    for name, schema in generated.items():
        assert load_schema(name) == schema
        assert schema["$id"].endswith(f"/{name}.json")
        assert f"/{CLOUD_SCHEMA_VERSION}/" in schema["$id"]


def test_descriptor_contract_is_strict_and_versioned() -> None:
    payload = {
        "schema_version": "v1alpha1",
        "name": "greeter",
        "version": "1.0.0",
        "a2a_protocol_version": "0.3.0",
    }
    assert AgentDescriptorContract.model_validate(payload).name == "greeter"
    with pytest.raises(ValidationError):
        AgentDescriptorContract.model_validate({**payload, "future_field": True})
    with pytest.raises(ValueError, match="invalid descriptor"):
        AgentDescriptor.from_dict({**payload, "future_field": True})


def test_config_record_and_observability_contracts_bound_payload_shapes() -> None:
    record = ConfigRecordContract.model_validate(
        {
            "agent": "greeter",
            "kind": "overlay",
            "version": 1,
            "digest": "a" * 64,
            "created_at": 1.0,
            "payload": {"model_endpoint": "https://model.example"},
        }
    )
    assert record.kind == "overlay"

    batch = ObservabilityBatchContract.model_validate(
        {
            "events": [
                {
                    "kind": "audit",
                    "trace_id": "trace-1",
                    "agent": "greeter",
                    "future_metric": 1,
                }
            ]
        }
    )
    assert batch.events[0].model_extra == {"future_metric": 1}
    with pytest.raises(ValidationError):
        ObservabilityBatchContract.model_validate(
            {"events": [{"kind": "unknown", "trace_id": "trace-1", "agent": "greeter"}]}
        )
