
import pytest

from agentguard.core.outcome import effective_pass
from agentguard.core.result import AssertionResult, RunResult


@pytest.mark.parametrize(
    "passed,status,cleanup_status,expected",
    [
        (True, "passed", "not_required", True),
        (True, "passed", "verified", True),
        (True, "passed", "unverified", False),
        (True, "failed", "verified", False),
        (False, "passed", "verified", False),
        (False, "failed", "verified", False),
        (False, "error", "verified", False),
    ],
)
def test_effective_pass_contract(
    passed,
    status,
    cleanup_status,
    expected,
):
    result = RunResult(
        scenario_name="outcome-test",
        passed=passed,
        status=status,
        cleanup_status=cleanup_status,
        evaluation_status="complete",
        assertions=[AssertionResult(name="checked", passed=True)],
    )

    assert effective_pass(result) is expected


def test_failed_assertion_cannot_be_accepted():
    result = RunResult(
        scenario_name="assertion-test",
        passed=True,
        status="passed",
        cleanup_status="verified",
        evaluation_status="complete",
        assertions=[
            AssertionResult(
                name="forbidden-change",
                passed=False,
            )
        ],
    )

    assert effective_pass(result) is False


@pytest.mark.parametrize("evaluation_status", ["not_run", "error", "complete"])
def test_empty_evaluation_never_passes(evaluation_status):
    assert not effective_pass(RunResult(
        scenario_name="empty", passed=True, evaluation_status=evaluation_status,
    ))


@pytest.mark.parametrize("changes", [
    {"evaluation_status": "not_run"},
    {"evaluation_status": "error"},
    {"error_type": "RuntimeError"},
    {"error_message": "inconsistent error"},
    {"error_stage": "evaluation"},
    {"evaluation_error_type": "ValueError"},
    {"evaluation_error_message": "missing evidence"},
])
def test_incomplete_or_inconsistent_results_block(changes):
    data = dict(scenario_name="checked", passed=True, evaluation_status="complete",
                assertions=[AssertionResult(name="checked", passed=True)])
    data.update(changes)
    assert not effective_pass(RunResult(**data))
