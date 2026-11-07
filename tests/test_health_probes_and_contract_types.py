"""Gap-closing tests: active health probes and every contract primitive type."""

import pytest

from micro_agent.definition.contracts import (
    ContractValidationError,
    _matches_type,
    validate_input,
    validate_output,
)
from micro_agent.definition.models import InputContract, OutputContract, ParameterDefinition
from micro_agent.health import HealthChecker, HealthStatus

# ---------------------------------------------------------------------------
# HealthChecker active probes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_readiness_maps_bool_status_and_exception_results():
    checker = HealthChecker()

    async def ok() -> bool:
        return True

    async def down() -> bool:
        return False

    async def degraded() -> HealthStatus:
        return HealthStatus.DEGRADED

    async def boom() -> bool:
        raise RuntimeError("probe exploded")

    checker.add_dependency("ok", probe=ok)
    checker.add_dependency("degraded", probe=degraded)
    result = await checker.probe_readiness()
    assert result.is_ready
    assert result.status is HealthStatus.DEGRADED

    checker.add_dependency("down", probe=down)
    result = await checker.probe_readiness()
    assert not result.is_ready
    assert result.status is HealthStatus.UNHEALTHY

    checker.add_dependency("boom", probe=boom)
    result = await checker.probe_readiness()
    by_name = {dep.name: dep for dep in result.dependencies}
    assert by_name["boom"].status is HealthStatus.UNHEALTHY
    assert by_name["boom"].details == {"error": "probe exploded"}
    assert by_name["ok"].status is HealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_probe_recovery_restores_readiness():
    checker = HealthChecker()
    state = {"up": False}

    async def probe() -> bool:
        return state["up"]

    checker.add_dependency("db", probe=probe)
    assert not (await checker.probe_readiness()).is_ready
    state["up"] = True
    result = await checker.probe_readiness()
    assert result.is_ready
    assert result.status is HealthStatus.HEALTHY


def test_liveness_reflects_alive_flag_and_probe():
    assert HealthChecker().check_liveness().alive

    checker = HealthChecker(liveness_probe=lambda: False)
    assert checker.check_liveness().details == {"reason": "liveness probe failed"}

    checker = HealthChecker()
    checker.set_alive(False)
    assert checker.check_liveness().details == {"reason": "process marked dead"}


def test_update_status_unknown_dependency_and_details():
    checker = HealthChecker()
    checker.add_dependency("db")
    assert checker.update_status("missing", HealthStatus.UNHEALTHY) is False
    assert checker.update_status("db", HealthStatus.DEGRADED, {"lag": 3}) is True
    dep = checker.check_readiness().dependencies[0]
    assert (dep.status, dep.details) == (HealthStatus.DEGRADED, {"lag": 3})


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_name", "good", "bad"),
    [
        ("any", object(), None),  # "any" never rejects; bad is unused below
        ("null", None, 0),
        ("string", "s", 1),
        ("integer", 3, True),
        ("number", 1.5, True),
        ("boolean", False, 0),
        ("object", {"a": 1}, []),
        ("array", [1], {}),
    ],
)
def test_matches_type_primitives(type_name, good, bad):
    assert _matches_type(good, type_name)
    if type_name != "any":
        assert not _matches_type(bad, type_name)


def test_matches_type_unknown_type_never_matches():
    assert _matches_type("x", "uuid") is False


def test_number_accepts_int_and_float_but_integer_rejects_float():
    assert _matches_type(2, "number") and _matches_type(2.5, "number")
    assert not _matches_type(2.5, "integer")


def test_contract_reports_missing_wrong_type_and_unknown_fields_together():
    contract = InputContract(
        parameters=[
            ParameterDefinition(name="id", type="string"),
            ParameterDefinition(name="count", type="integer"),
            ParameterDefinition(name="note", type="string", required=False),
        ]
    )
    with pytest.raises(ContractValidationError) as info:
        validate_input(contract, {"count": "many", "extra": 1})
    assert info.value.contract == "input"
    assert info.value.errors == [
        "missing required field 'id'",
        "field 'count' must be integer, got str",
        "unknown field(s): extra",
    ]


def test_contract_requires_json_object_and_allows_optional_omission():
    contract = OutputContract(
        parameters=[
            ParameterDefinition(name="answer", type="string"),
            ParameterDefinition(name="meta", type="object", required=False),
        ]
    )
    validate_output(contract, {"answer": "yes"})
    with pytest.raises(ContractValidationError, match="must be a JSON object"):
        validate_output(contract, ["answer"])


def test_empty_contract_is_unconstrained():
    validate_input(InputContract(), "anything")
    validate_output(OutputContract(), 42)
