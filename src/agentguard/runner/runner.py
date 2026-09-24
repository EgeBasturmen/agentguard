
from agentguard.core.result import (
    AssertionResult,
    RunResult,
)
from agentguard.core.scenario import Scenario
from agentguard.core.snapshot import StateSnapshot
from agentguard.evaluation.assertions import (
    evaluate_expected_state,
    evaluate_forbidden_change,
    evaluate_tool_expectation,
)
from agentguard.evaluation.diff import diff_states
from agentguard.evaluation.invariants import evaluate_invariant
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.tracing.collector import TraceCollector
from agentguard.sandbox.base import SandboxFactory

class ScenarioRunner:

    def __init__(
        self,
        sandbox_factory: SandboxFactory = CommerceSandbox,
    )->None:
        self.sandbox_factory= sandbox_factory

    def run(self, scenario: Scenario, agent) -> RunResult:
        tracer = TraceCollector()

        sandbox = self.sandbox_factory(
            initial_state=scenario.initial_state,
            tracer=tracer,
        )

        before = StateSnapshot.capture(
            sandbox.get_state()
        )

        agent_error = None

        tracer.emit(
            "run_started",
            payload={
                "scenario": scenario.name,
            },
        )

        try:
            agent.run(
                task=scenario.task,
                sandbox=sandbox,
            )

        except Exception as exc:
            agent_error = exc

            tracer.emit(
                "error",
                payload={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )

        finally:
            tracer.emit(
                "run_completed",
                payload={
                    "success": agent_error is None,
                },
            )

        after = StateSnapshot.capture(
            sandbox.get_state()
        )

        mutations = diff_states(
            before.state,
            after.state,
        )

        assertions = []

        for expected in scenario.expected_state:
            assertions.append(
                evaluate_expected_state(
                    after.state,
                    expected,
                )
            )

        for forbidden in scenario.forbidden_changes:
            assertions.append(
                evaluate_forbidden_change(
                    mutations,
                    forbidden,
                )
            )

        for expectation in scenario.tool_expectations:
            assertions.append(
                evaluate_tool_expectation(
                    tracer.events,
                    expectation,
                )
            )

        for invariant in scenario.invariants:
            assertions.append(
                evaluate_invariant(
                    after.state,
                    invariant,
                )
            )

        if agent_error is not None:
            assertions.append(
                AssertionResult(
                    name="agent:execution",
                    passed=False,
                    message=(
                        f"{type(agent_error).__name__}: "
                        f"{agent_error}"
                    ),
                )
            )

        passed = all(
            assertion.passed
            for assertion in assertions
        )

        if agent_error is not None:
            status = "error"

        elif passed:
            status = "passed"

        else:
            status = "failed"

        return RunResult(
            scenario_name=scenario.name,
            passed=passed,
            status=status,
            assertions=assertions,
            mutations=mutations,
            traces=tracer.events,
            error_type=(
                type(agent_error).__name__
                if agent_error is not None
                else None
            ),
            error_message=(
                str(agent_error)
                if agent_error is not None
                else None
            ),
        )