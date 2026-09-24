from typing import Any

from agentguard.core.result import AssertionResult
from agentguard.core.scenario import ExpectedState

from agentguard.core.mutation import StateMutation
from agentguard.core.scenario import ForbiddenChange
from agentguard.core.scenario import ToolExpectation
from agentguard.core.trace import TraceEvent

def get_path(
    state: dict[str, Any],
    path: str,
) -> Any:

    value: Any = state

    for part in path.split("."):
        value = value[part]

    return value

def evaluate_tool_expectation(
    traces: list[TraceEvent],
    expectation: ToolExpectation,
) -> AssertionResult:

    calls = [
        event
        for event in traces
        if (
            event.event_type == "tool_started"
            and event.tool_name == expectation.tool_name
        )
    ]

    count = len(calls)

    if expectation.times is not None:

        passed = count == expectation.times

        return AssertionResult(
            name=f"tool:{expectation.tool_name}",
            passed=passed,
            message=(
                None
                if passed
                else (
                    f"Expected {expectation.times} calls, "
                    f"got {count}"
                )
            ),
        )

    if expectation.max_times is not None:

        passed = count <= expectation.max_times

        return AssertionResult(
            name=f"tool:{expectation.tool_name}",
            passed=passed,
            message=(
                None
                if passed
                else (
                    f"Expected at most {expectation.max_times} calls, "
                    f"got {count}"
                )
            ),
        )

    return AssertionResult(
        name=f"tool:{expectation.tool_name}",
        passed=True,
    )

def evaluate_expected_state(
    state: dict[str, Any],
    expectation: ExpectedState,
) -> AssertionResult:

    try:
        actual = get_path(
            state,
            expectation.path,
        )

    except KeyError:

        return AssertionResult(
            name=f"expected:{expectation.path}",
            passed=False,
            message="Path does not exist.",
        )

    passed = actual == expectation.value

    return AssertionResult(
        name=f"expected:{expectation.path}",
        passed=passed,
        message=(
            None
            if passed
            else (
                f"Expected {expectation.value!r}, "
                f"got {actual!r}"
            )
        ),
    )




def evaluate_forbidden_change(
    mutations: list[StateMutation],
    forbidden: ForbiddenChange,
) -> AssertionResult:

    matching = [
        mutation
        for mutation in mutations
        if (
            mutation.path == forbidden.path
            or mutation.path.startswith(
                forbidden.path + "."
            )
        )
    ]

    passed = len(matching) == 0

    return AssertionResult(
        name=f"unchanged:{forbidden.path}",
        passed=passed,
        message=(
            None
            if passed
            else f"Unexpected mutation: {matching}"
        ),
    )