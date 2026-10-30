
from typer.testing import CliRunner

from agentguard.cli.main import app


runner = CliRunner()

SCENARIO = "scenarios/refund_order.yaml"


def test_good_agent_cli_allows():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:GoodRefundAgent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ALLOW" in result.output


def test_bad_agent_cli_blocks():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:BadRefundAgent",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output


def test_duplicate_agent_cli_blocks():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:DuplicateRefundAgent",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output


def test_unknown_agent():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "does.not.exist:Agent",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Agent error" in result.output


def test_missing_scenario():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            "scenarios/does_not_exist.yaml",
            "--agent",
            "examples.refund_agent:GoodRefundAgent",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Scenario error" in result.output


def test_regression_duplicate_refund_blocks():
    result = runner.invoke(
        app,
        [
            "regression",
            "--scenario",
            SCENARIO,
            "--baseline",
            "examples.refund_agent:GoodRefundAgent",
            "--candidate",
            "examples.refund_agent:DuplicateRefundAgent",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output
    assert "4 blocking regression finding(s)" in result.output


def test_regression_same_agent_allows():
    result = runner.invoke(
        app,
        [
            "regression",
            "--scenario",
            SCENARIO,
            "--baseline",
            "examples.refund_agent:GoodRefundAgent",
            "--candidate",
            "examples.refund_agent:GoodRefundAgent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ALLOW" in result.output

def test_crashing_agent_cli_blocks():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:CrashingRefundAgent",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output
    assert "RuntimeError" in result.output
    assert "Payment provider connection lost" in result.output


def test_file_sandbox_cli_allows():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            "scenarios/read_file.yaml",
            "--agent",
            "examples.file_agent:SafeFileAgent",
            "--sandbox",
            "agentguard.sandbox.files:FileSandbox",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ALLOW" in result.output


def test_file_sandbox_cli_blocks():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            "scenarios/read_file.yaml",
            "--agent",
            "examples.file_agent:UnsafeFileAgent",
            "--sandbox",
            "agentguard.sandbox.files:FileSandbox",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output


def test_unknown_sandbox_cli():
    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            "scenarios/read_file.yaml",
            "--agent",
            "examples.file_agent:SafeFileAgent",
            "--sandbox",
            "unknown_module:UnknownSandbox",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Sandbox error" in result.output



def test_unverified_cleanup_cli_blocks(monkeypatch):
    from agentguard.core.result import RunResult
    from agentguard.cli import main as cli_main

    def fake_run(self, scenario, agent):
        return RunResult(
            scenario_name="cleanup-test",
            passed=True,
            status="passed",
            cleanup_status="unverified",
        )

    monkeypatch.setattr(
        cli_main.ScenarioRunner,
        "run",
        fake_run,
    )

    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:GoodRefundAgent",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output
    assert "ALLOW" not in result.output


def test_verified_cleanup_cli_allows(monkeypatch):
    from agentguard.core.result import RunResult
    from agentguard.cli import main as cli_main

    def fake_run(self, scenario, agent):
        return RunResult(
            scenario_name="cleanup-test",
            passed=True,
            status="passed",
            cleanup_status="verified",
        )

    monkeypatch.setattr(
        cli_main.ScenarioRunner,
        "run",
        fake_run,
    )

    result = runner.invoke(
        app,
        [
            "test",
            "--scenario",
            SCENARIO,
            "--agent",
            "examples.refund_agent:GoodRefundAgent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ALLOW" in result.output
