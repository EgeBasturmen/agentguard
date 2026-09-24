from agentguard.runner.runner import ScenarioRunner
from agentguard.spec.loader import load_scenario

from examples.document_agent import (
    SafeDocumentAgent,
    UnsafeDocumentAgent,
)
from examples.document_sandbox import DocumentSandbox


def test_document_sandbox_registers_tools_automatically():
    scenario = load_scenario(
        "scenarios/read_document.yaml"
    )

    runner = ScenarioRunner(
        sandbox_factory=DocumentSandbox,
    )

    result = runner.run(
        scenario=scenario,
        agent=SafeDocumentAgent(),
    )

    assert result.passed is True
    assert result.status == "passed"

    assert any(
        event.event_type == "tool_started"
        and event.tool_name == "document.read"
        for event in result.traces
    )


def test_document_sandbox_blocks_forbidden_tool():
    scenario = load_scenario(
        "scenarios/read_document.yaml"
    )

    runner = ScenarioRunner(
        sandbox_factory=DocumentSandbox,
    )

    result = runner.run(
        scenario=scenario,
        agent=UnsafeDocumentAgent(),
    )

    assert result.passed is False
    assert result.status == "error"
    assert result.error_type == "ToolPermissionError"

    assert result.mutations == []

    assert any(
        event.event_type == "error"
        and event.tool_name == "document.delete"
        for event in result.traces
    )