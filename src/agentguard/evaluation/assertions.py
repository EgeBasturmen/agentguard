from typing import Any

from agentguard.core.result import AssertionResult
from agentguard.core.scenario import ExpectedState

from agentguard.core.mutation import StateMutation
from agentguard.core.scenario import ForbiddenChange
from agentguard.core.scenario import ToolExpectation
from agentguard.core.trace import TraceEvent
from agentguard.core.paths import PathResolutionError, parse_path, resolve_path, values_equal

def get_path(
    state: dict[str, Any],
    path: str,
) -> Any:

    return resolve_path(state, parse_path(path))

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
        passed=False,
        message="Tool expectation has no call limit.",
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

    except PathResolutionError as exc:

        return AssertionResult(
            name=f"expected:{expectation.path}",
            passed=False,
            message=str(exc),
        )

    passed = values_equal(actual, expectation.value)

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

    protected = parse_path(forbidden.path)
    matching = []
    for mutation in mutations:
        changed = parse_path(mutation.path)
        if changed[:len(protected)] == protected:
            matching.append(mutation)
        elif protected[:len(changed)] == changed:
            # Lists and deleted/replaced parents are emitted as one mutation.
            # Compare the protected descendant to avoid blocking sibling edits.
            suffix = protected[len(changed):]
            missing = object()

            def descendant(value: Any, absent: bool) -> Any:
                if absent:
                    return missing
                try:
                    return resolve_path(value, suffix)
                except PathResolutionError:
                    return missing

            before = descendant(mutation.before, mutation.mutation_type == "created")
            after = descendant(mutation.after, mutation.mutation_type == "deleted")
            if not values_equal(before, after):
                matching.append(mutation)

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
