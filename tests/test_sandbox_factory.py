from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.files import FileSandbox
from agentguard.spec.loader import load_scenario

from examples.file_agent import (
    SafeFileAgent,
    UnsafeFileAgent,
)


def test_safe_file_agent_passes():
    scenario = load_scenario(
        "scenarios/read_file.yaml"
    )

    runner = ScenarioRunner(
        sandbox_factory=FileSandbox,
    )

    result = runner.run(
        scenario=scenario,
        agent=SafeFileAgent(),
    )

    assert result.passed is True
    assert result.status == "passed"
    assert result.mutations == []


def test_unsafe_file_agent_blocks():
    scenario = load_scenario(
        "scenarios/read_file.yaml"
    )

    runner = ScenarioRunner(
        sandbox_factory=FileSandbox,
    )

    result = runner.run(
        scenario=scenario,
        agent=UnsafeFileAgent(),
    )

    assert result.passed is False
    assert result.status == "failed"

    assert any(
        mutation.path == "files.important"
        and mutation.mutation_type == "deleted"
        for mutation in result.mutations
    )

    assert any(
        assertion.name == "tool:file.delete"
        and assertion.passed is False
        for assertion in result.assertions
    )