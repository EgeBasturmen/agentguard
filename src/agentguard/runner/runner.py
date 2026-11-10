"""Local scenario execution and fail-closed evaluation."""

import inspect
from typing import Callable

from agentguard.core.result import AssertionResult, RunResult
from agentguard.core.manifest import ComparisonManifest, check_id
from agentguard.core.paths import parse_path
from agentguard.core.scenario import Scenario
from agentguard.core.snapshot import StateSnapshot
from agentguard.evaluation.assertions import (
    evaluate_expected_state,
    evaluate_forbidden_change,
    evaluate_tool_expectation,
)
from agentguard.evaluation.diff import diff_states
from agentguard.evaluation.invariants import evaluate_invariant
from agentguard.sandbox.base import SandboxFactory
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.tracing.collector import TraceCollector
from agentguard.contracts.compatibility import local_context


class UnsupportedAsyncAgentError(TypeError):
    """The local runner currently requires a synchronous agent."""


class ScenarioRunner:
    def __init__(
        self, sandbox_factory: SandboxFactory = CommerceSandbox, *,
        environment_id: str | None = None,
    ) -> None:
        self.sandbox_factory = sandbox_factory
        factory_type = type(sandbox_factory)
        self.environment_id = environment_id or (
            f"{getattr(sandbox_factory, '__module__', factory_type.__module__)}:"
            f"{getattr(sandbox_factory, '__qualname__', factory_type.__qualname__)}"
        )

    def run(self, scenario: Scenario, agent) -> RunResult:
        tracer = TraceCollector()
        result = RunResult(
            scenario_name=scenario.name, passed=False, status="error",
            execution_context=local_context(),
        )

        def record_error(exc: Exception, stage: str) -> None:
            result.status = "error"
            result.passed = False
            result.error_type = type(exc).__name__
            result.error_message = str(exc)
            result.error_stage = stage
            tracer.emit("error", payload={
                "stage": stage,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })

        stage = "validation"
        try:
            # Revalidate model instances too: callers may have mutated a spec.
            scenario = Scenario.model_validate(scenario.model_dump())
            result.comparison_manifest = ComparisonManifest(
                scenario=scenario,
                environment=self.environment_id,
            )
            stage = "provision"
            sandbox = self.sandbox_factory(
                initial_state=scenario.initial_state, tracer=tracer,
            )
            stage = "snapshot"
            before = StateSnapshot.capture(sandbox.get_state())
        except Exception as exc:
            record_error(exc, stage)
            result.traces = tracer.events
            return result

        tracer.emit("run_started", payload={"scenario": scenario.name})
        agent_error = None
        try:
            if inspect.iscoroutinefunction(agent.run) or inspect.isasyncgenfunction(agent.run):
                raise UnsupportedAsyncAgentError("ScenarioRunner requires a synchronous run method.")
            output = agent.run(task=scenario.task, sandbox=sandbox)
            if inspect.isawaitable(output) or inspect.isasyncgen(output):
                if inspect.iscoroutine(output):
                    output.close()
                raise UnsupportedAsyncAgentError("Agent returned an unsupported asynchronous result.")
        except Exception as exc:
            agent_error = exc
            record_error(exc, "execution")
        finally:
            tracer.emit("run_completed", payload={"success": agent_error is None})

        stage = "snapshot"
        try:
            after = StateSnapshot.capture(sandbox.get_state())
            stage = "diff"
            result.mutations = diff_states(before.state, after.state)
        except Exception as exc:
            record_error(exc, stage)
            result.traces = tracer.events
            return result

        evaluations: list[tuple[str, str, Callable[[], AssertionResult]]] = []
        for expected in scenario.expected_state:
            evaluations.append((f"expected:{expected.path}", check_id("expected", list(parse_path(expected.path))), lambda item=expected: evaluate_expected_state(after.state, item)))
        for forbidden in scenario.forbidden_changes:
            evaluations.append((f"unchanged:{forbidden.path}", check_id("unchanged", list(parse_path(forbidden.path))), lambda item=forbidden: evaluate_forbidden_change(result.mutations, item)))
        for expectation in scenario.tool_expectations:
            evaluations.append((f"tool:{expectation.tool_name}", check_id("tool", expectation.tool_name), lambda item=expectation: evaluate_tool_expectation(tracer.events, item)))
        for invariant in scenario.invariants:
            evaluations.append((f"invariant:{invariant.name}", check_id("invariant", invariant.name), lambda item=invariant: evaluate_invariant(after.state, item)))

        result.evaluation_status = "complete"
        for name, assertion_id, evaluate in evaluations:
            try:
                assertion = evaluate()
                if not isinstance(assertion, AssertionResult):
                    raise TypeError("Evaluator must return AssertionResult.")
                assertion.assertion_id = assertion_id
                result.assertions.append(assertion)
            except Exception as exc:
                result.evaluation_status = "error"
                if result.evaluation_error_type is None:
                    result.evaluation_error_type = type(exc).__name__
                    result.evaluation_error_message = str(exc)
                    record_error(exc, "evaluation")
                result.assertions.append(AssertionResult(
                    name=name, assertion_id=assertion_id, passed=False,
                    message=f"Evaluation error ({type(exc).__name__}): {exc}",
                ))

        if agent_error is not None:
            result.assertions.append(AssertionResult(
                name="agent:execution", passed=False,
                message=f"{type(agent_error).__name__}: {agent_error}",
            ))

        if agent_error is None and result.evaluation_status == "complete":
            result.passed = bool(result.assertions) and all(a.passed for a in result.assertions)
            result.status = "passed" if result.passed else "failed"
        result.traces = tracer.events
        return result
