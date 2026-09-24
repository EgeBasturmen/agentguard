import pytest

from agentguard.spec.loader import (
    ScenarioLoadError,
    load_scenario,
)


def test_load_refund_scenario():

    scenario = load_scenario(
        "scenarios/refund_order.yaml"
    )

    assert scenario.name == "refund-order"
    assert scenario.task == "Refund order ORD-100"

    assert (
        scenario.initial_state["orders"]["ORD-100"]["total"]
        == 80.0
    )

    assert len(scenario.expected_state) == 1
    assert len(scenario.forbidden_changes) == 2
    assert len(scenario.tool_expectations) == 1
    assert len(scenario.invariants) == 1


def test_missing_scenario_raises_error():

    with pytest.raises(ScenarioLoadError):
        load_scenario(
            "scenarios/does_not_exist.yaml"
        )

def test_invalid_scenario_raises_error():

    with pytest.raises(ScenarioLoadError):
        load_scenario(
            "tests/fixtures/invalid_scenario.yaml"
        )