from agentguard.core.result import RunResult


def test_cleanup_status_defaults_to_not_required():
    result = RunResult(
        scenario_name="refund-order",
        passed=True,
    )

    assert result.cleanup_status == "not_required"


def test_cleanup_status_can_be_verified():
    result = RunResult(
        scenario_name="refund-order",
        passed=True,
        cleanup_status="verified",
    )

    assert result.cleanup_status == "verified"


def test_cleanup_status_can_be_unverified():
    result = RunResult(
        scenario_name="refund-order",
        passed=False,
        status="error",
        cleanup_status="unverified",
    )

    assert result.cleanup_status == "unverified"