import inspect
import json

import pytest
from typer.testing import CliRunner

from agentguard.cli.main import app
from agentguard.core.outcome import effective_pass
from agentguard.core.scenario import Scenario
from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.generic import GenericSandbox


class Noop:
    def run(self, task, sandbox):
        return "done"


def spec(**changes):
    data = dict(name="generic", task="Inspect a value", initial_state={"a": 1},
                expected_state=[{"path": "a", "value": 1}])
    data.update(changes)
    return Scenario(**data)


def test_mutated_empty_spec_is_rejected_before_agent_execution():
    scenario = spec()
    scenario.expected_state.clear()
    class MustNotRun:
        def run(self, **kwargs):
            pytest.fail("Invalid spec reached the agent")
    result = ScenarioRunner(GenericSandbox).run(scenario, MustNotRun())
    assert result.status == "error"
    assert result.error_stage == "validation"
    assert result.evaluation_status == "not_run"
    assert not effective_pass(result)


@pytest.mark.parametrize("stage", ["provision", "snapshot", "diff"])
def test_environment_and_diff_errors_are_typed(stage, monkeypatch):
    if stage == "diff":
        from agentguard.runner import runner
        def broken_diff(*args):
            raise RuntimeError("diff failed")
        monkeypatch.setattr(runner, "diff_states", broken_diff)
    class BrokenSandbox(GenericSandbox):
        def __init__(self, *args, **kwargs):
            if stage == "provision":
                raise RuntimeError("provision failed")
            super().__init__(*args, **kwargs)
        def get_state(self):
            if stage == "snapshot":
                raise RuntimeError("snapshot failed")
            return super().get_state()
    result = ScenarioRunner(BrokenSandbox).run(spec(), Noop())
    assert result.error_stage == stage
    assert result.status == "error"
    assert result.evaluation_status == "not_run"
    assert not effective_pass(result)


def test_final_snapshot_error_is_typed():
    class BrokenAfterRun(GenericSandbox):
        calls = 0
        def get_state(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("final snapshot failed")
            return super().get_state()
    result = ScenarioRunner(BrokenAfterRun).run(spec(), Noop())
    assert result.error_stage == "snapshot"
    assert not effective_pass(result)


def test_async_agent_does_not_pass_or_run():
    class AsyncAgent:
        async def run(self, task, sandbox):
            pytest.fail("Async body must not run in a synchronous runner")
    result = ScenarioRunner(GenericSandbox).run(spec(), AsyncAgent())
    assert result.error_type == "UnsupportedAsyncAgentError"
    assert result.error_stage == "execution"
    assert result.evaluation_status == "complete"
    assert not effective_pass(result)


def test_returned_coroutine_is_closed_and_rejected():
    class WrappedAsync:
        returned = None
        def run(self, task, sandbox):
            async def work():
                pass
            self.returned = work()
            return self.returned
    agent = WrappedAsync()
    result = ScenarioRunner(GenericSandbox).run(spec(), agent)
    assert result.error_type == "UnsupportedAsyncAgentError"
    assert inspect.getcoroutinestate(agent.returned) == inspect.CORO_CLOSED
    assert not effective_pass(result)


def test_async_generator_agent_is_rejected():
    class AsyncGenerator:
        async def run(self, task, sandbox):
            yield "not an execution result"
    result = ScenarioRunner(GenericSandbox).run(spec(), AsyncGenerator())
    assert result.error_type == "UnsupportedAsyncAgentError"
    assert not effective_pass(result)


@pytest.mark.parametrize("initial", [
    {},
    {"orders": {"one": {"total": True}}, "refunds": []},
    {"orders": {"one": {"total": 1}}, "refunds": {}},
    {"orders": {"one": {"total": 1}}, "refunds": [{"order_id": "one"}]},
])
def test_invalid_invariant_fixture_is_evaluation_error(initial):
    scenario = spec(initial_state=initial, expected_state=[], invariants=[{
        "name": "legacy-check", "kind": "refund_total_lte_order_total",
        "params": {"order_id": "one"},
    }])
    result = ScenarioRunner(GenericSandbox).run(scenario, Noop())
    assert result.status == "error"
    assert result.error_stage == "evaluation"
    assert result.evaluation_status == "error"
    assert result.evaluation_error_type == "InvariantEvaluationError"
    assert not effective_pass(result)


def test_evaluator_errors_preserve_other_checks(monkeypatch):
    from agentguard.runner import runner
    scenario = spec(expected_state=[{"path": "a", "value": 1}, {"path": "b", "value": 2}])
    real_evaluate = runner.evaluate_expected_state
    def broken(state, expected):
        if expected.path == "a":
            raise RuntimeError("<bad evidence>")
        return real_evaluate(state, expected)
    monkeypatch.setattr(runner, "evaluate_expected_state", broken)
    result = ScenarioRunner(GenericSandbox).run(scenario, Noop())
    assert result.evaluation_status == "error"
    assert len(result.assertions) == 2
    assert not any(a.passed for a in result.assertions)
    assert result.evaluation_error_message == "<bad evidence>"


def test_invalid_evaluator_return_blocks(monkeypatch):
    from agentguard.runner import runner
    monkeypatch.setattr(runner, "evaluate_expected_state", lambda *args: True)
    result = ScenarioRunner(GenericSandbox).run(spec(), Noop())
    assert result.evaluation_status == "error"
    assert result.evaluation_error_type == "TypeError"
    assert not effective_pass(result)


def test_missing_expected_path_is_behavior_failure():
    result = ScenarioRunner(GenericSandbox).run(spec(initial_state={}), Noop())
    assert result.status == "failed"
    assert result.evaluation_status == "complete"
    assert result.evaluation_error_type is None
    assert not effective_pass(result)


@pytest.mark.parametrize("broken", [False, True])
def test_cli_json_and_html_share_outcome(tmp_path, monkeypatch, broken):
    from agentguard.cli import main
    result = ScenarioRunner(GenericSandbox).run(spec(), Noop())
    if broken:
        result.evaluation_status = "error"
        result.evaluation_error_type = "ValueError"
        result.evaluation_error_message = "<bad evidence>"
    monkeypatch.setattr(main.ScenarioRunner, "run", lambda *args, **kwargs: result)
    json_path = tmp_path / "result.json"
    html_path = tmp_path / "result.html"
    invocation = CliRunner().invoke(app, [
        "test", "--trusted-local", "--agent", "examples.refund_agent:GoodRefundAgent",
        "--scenario", "scenarios/refund_order.yaml",
        "--json-report", str(json_path), "--html-report", str(html_path),
    ])
    assert invocation.exit_code == (1 if broken else 0), invocation.output
    assert ("BLOCK" if broken else "ALLOW") in invocation.output
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["verdict"] == ("FAIL" if broken else "PASS")
    assert payload["result"]["evaluation_status"] == result.evaluation_status
    html = html_path.read_text(encoding="utf-8")
    assert "Evaluation status" in html
    if broken:
        assert "&lt;bad evidence&gt;" in html
        assert "<bad evidence>" not in html


def test_cli_rejects_invalid_spec_before_import(tmp_path, monkeypatch):
    from agentguard.cli import main
    path = tmp_path / "invalid.yaml"
    path.write_text("name: empty\ntask: x\ninitial_state: {}\n", encoding="utf-8")
    def forbidden(*args):
        pytest.fail("Invalid spec must be rejected before agent loading")
    monkeypatch.setattr(main, "load_python_agent", forbidden)
    result = CliRunner().invoke(app, ["test", "--trusted-local", "--agent", "untrusted:Agent", "--scenario", str(path)])
    assert result.exit_code == 2
    assert "Scenario error" in result.output
