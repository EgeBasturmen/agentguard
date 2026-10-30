
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
    )

    assert effective_pass(result) is expected


def test_failed_assertion_cannot_be_accepted():
    result = RunResult(
        scenario_name="assertion-test",
        passed=True,
        status="passed",
        cleanup_status="verified",
        assertions=[
            AssertionResult(
                name="forbidden-change",
                passed=False,
            )
        ],
    )

    assert effective_pass(result) is False
