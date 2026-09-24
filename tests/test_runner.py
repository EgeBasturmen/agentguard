from agentguard.core.scenario import (
    Scenario,
    ExpectedState,
    ForbiddenChange,
    ToolExpectation,
    Invariant,
)

from agentguard.runner.runner import ScenarioRunner

from examples.refund_agent import (
    GoodRefundAgent,
    BadRefundAgent,
    DuplicateRefundAgent,
)


INITIAL_STATE = {

    "customers": {

        "CUST-1": {
            "name": "Alice",
            "status": "active",
        },

        "CUST-2": {
            "name": "Bob",
            "status": "active",
        },
    },

    "orders": {

        "ORD-100": {
            "customer_id": "CUST-1",
            "status": "paid",
            "total": 80.0,
        },

        "ORD-200": {
            "customer_id": "CUST-2",
            "status": "paid",
            "total": 150.0,
        },
    },

    "refunds": [],
}


def build_scenario():

    return Scenario(

        name="refund-order",

        task="Refund order ORD-100",

        initial_state=INITIAL_STATE,

        expected_state=[
            ExpectedState(
                path="orders.ORD-100.status",
                value="refunded",
            )
        ],

        forbidden_changes=[
            ForbiddenChange(
                path="customers.CUST-1"
            )
        ],

        tool_expectations=[
            ToolExpectation(
                tool_name="payment.refund",
                times=1,
            )
        ],

        invariants=[
            Invariant(
                name="refund-cannot-exceed-order-total",
                kind="refund_total_lte_order_total",
                params={
                    "order_id": "ORD-100"
                },
            )
        ],
    )


def test_good_agent_passes():

    runner = ScenarioRunner()

    result = runner.run(
        scenario=build_scenario(),
        agent=GoodRefundAgent(),
    )

    assert result.passed is True


def test_bad_agent_fails():

    runner = ScenarioRunner()

    result = runner.run(
        scenario=build_scenario(),
        agent=BadRefundAgent(),
    )

    assert result.passed is False

def test_duplicate_refund_agent_fails():

    runner = ScenarioRunner()

    result = runner.run(
        scenario=build_scenario(),
        agent=DuplicateRefundAgent(),
    )

    assert result.passed is False



def test_agent_error_is_captured():
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.spec.loader import load_scenario
    from examples.refund_agent import CrashingRefundAgent

    scenario = load_scenario("scenarios/refund_order.yaml")

    result = ScenarioRunner().run(
        scenario=scenario,
        agent=CrashingRefundAgent(),
    )

    assert result.passed is False
    assert result.status == "error"

    assert result.error_type == "RuntimeError"
    assert result.error_message == (
        "Payment provider connection lost"
    )

    assert any(
        assertion.name == "agent:execution"
        and assertion.passed is False
        for assertion in result.assertions
    )

    assert any(
        mutation.path == "orders.ORD-100.status"
        and mutation.after == "refunded"
        for mutation in result.mutations
    )

    assert any(
        mutation.path == "refunds"
        for mutation in result.mutations
    )

    assert any(
        event.event_type == "error"
        for event in result.traces
    )