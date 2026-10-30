from agentguard.core.scenario import (
    ExpectedState,
    ForbiddenChange,
    Invariant,
    Scenario,
    ToolExpectation,
)

from agentguard.regression.comparator import (
    RegressionComparator,
)

from agentguard.runner.runner import ScenarioRunner

from examples.refund_agent import (
    BadRefundAgent,
    DuplicateRefundAgent,
    GoodRefundAgent,
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


def build_scenario() -> Scenario:

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
                path="customers.CUST-1",
            ),
            ForbiddenChange(
                path="orders.ORD-200",
            ),
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
                    "order_id": "ORD-100",
                },
            )
        ],
    )

def test_duplicate_refund_is_regression():

    scenario = build_scenario()

    runner = ScenarioRunner()

    baseline = runner.run(
        scenario,
        GoodRefundAgent(),
    )

    candidate = runner.run(
        scenario,
        DuplicateRefundAgent(),
    )

    comparator = RegressionComparator()

    result = comparator.compare(
        baseline_name="good",
        baseline=baseline,
        candidate_name="duplicate",
        candidate=candidate,
    )

    assert result.passed is False

    finding_types = {
        finding.finding_type
        for finding in result.findings
    }

    assert "new_assertion_failure" in finding_types
    assert "tool_call_count_changed" in finding_types


def test_customer_delete_is_new_behavior():

    scenario = build_scenario()

    runner = ScenarioRunner()

    baseline = runner.run(
        scenario,
        GoodRefundAgent(),
    )

    candidate = runner.run(
        scenario,
        BadRefundAgent(),
    )

    comparator = RegressionComparator()

    result = comparator.compare(
        baseline_name="good",
        baseline=baseline,
        candidate_name="bad",
        candidate=candidate,
    )

    assert result.passed is False

    assert any(
        finding.finding_type == "new_tool_call"
        and finding.key == "customer.delete"
        for finding in result.findings
    )

    assert any(
        finding.finding_type == "new_state_mutation"
        and finding.key == "customers.CUST-1.status"
        for finding in result.findings
    )


def test_same_behavior_has_no_regression():

    scenario = build_scenario()

    runner = ScenarioRunner()

    baseline = runner.run(
        scenario,
        GoodRefundAgent(),
    )

    candidate = runner.run(
        scenario,
        GoodRefundAgent(),
    )

    comparator = RegressionComparator()

    result = comparator.compare(
        baseline_name="good-v1",
        baseline=baseline,
        candidate_name="good-v2",
        candidate=candidate,
    )

    assert result.passed is True
    assert result.blocking_regressions == 0


def test_execution_error_is_blocking_regression():
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.spec.loader import load_scenario
    from examples.refund_agent import (
        GoodRefundAgent,
        CrashingRefundAgent,
    )

    scenario = load_scenario("scenarios/refund_order.yaml")
    runner = ScenarioRunner()

    baseline = runner.run(
        scenario=scenario,
        agent=GoodRefundAgent(),
    )

    candidate = runner.run(
        scenario=scenario,
        agent=CrashingRefundAgent(),
    )

    result = RegressionComparator().compare(
        baseline_name="good",
        baseline=baseline,
        candidate_name="crashing",
        candidate=candidate,
    )

    assert result.passed is False

    assert any(
        finding.finding_type == "new_execution_error"
        and finding.severity == "critical"
        for finding in result.findings
    )


def test_existing_execution_error_still_blocks():
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.spec.loader import load_scenario
    from examples.refund_agent import CrashingRefundAgent

    scenario = load_scenario("scenarios/refund_order.yaml")
    runner = ScenarioRunner()

    baseline = runner.run(
        scenario=scenario,
        agent=CrashingRefundAgent(),
    )

    candidate = runner.run(
        scenario=scenario,
        agent=CrashingRefundAgent(),
    )

    result = RegressionComparator().compare(
        baseline_name="crashing-v1",
        baseline=baseline,
        candidate_name="crashing-v2",
        candidate=candidate,
    )

    assert result.passed is False

    assert any(
        finding.finding_type == "new_execution_error"
        for finding in result.findings
    )

def test_unverified_candidate_cleanup_blocks_regression():
    from agentguard.core.result import RunResult
    from agentguard.regression.comparator import RegressionComparator

    baseline = RunResult(
        scenario_name="cleanup-test",
        passed=True,
        status="passed",
        cleanup_status="verified",
    )

    candidate = RunResult(
        scenario_name="cleanup-test",
        passed=True,
        status="passed",
        cleanup_status="unverified",
    )

    result = RegressionComparator().compare(
        baseline_name="baseline",
        baseline=baseline,
        candidate_name="candidate",
        candidate=candidate,
    )

    assert result.passed is False
    assert any(
        finding.finding_type == "candidate_run_not_verified"
        for finding in result.findings
    )