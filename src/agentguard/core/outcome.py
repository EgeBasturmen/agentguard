
"""Centralized run outcome evaluation."""

from agentguard.core.result import RunResult


def effective_pass(result: RunResult) -> bool:
    """
    Return True only when a run is safe to classify as PASS.

    Cleanup verification is required when cleanup is applicable.
    """
    return (
        result.passed is True
        and result.status == "passed"
        and result.cleanup_status in {
            "not_required",
            "verified",
        }
        and all(assertion.passed for assertion in result.assertions)
    )
