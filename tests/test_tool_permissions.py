import pytest

from agentguard.sandbox.generic import (
    GenericSandbox,
    ToolPermissionError,
)
from agentguard.tracing.collector import TraceCollector

from examples.generic_tools import (
    delete_document,
    read_document,
)


def create_restricted_sandbox():
    tracer = TraceCollector()

    sandbox = GenericSandbox(
        initial_state={
            "documents": {
                "report": {"content": "Monthly report"},
                "important": {"content": "Do not delete"},
            }
        },
        tracer=tracer,
        allowed_tools={"document.read"},
    )

    sandbox.register_tool(
        "document.read",
        read_document,
    )

    sandbox.register_tool(
        "document.delete",
        delete_document,
    )

    return sandbox, tracer


def test_allowed_tool_runs():
    sandbox, tracer = create_restricted_sandbox()

    result = sandbox.call_tool(
        "document.read",
        document_id="report",
    )

    assert result == "Monthly report"

    assert [
        event.event_type
        for event in tracer.events
    ] == [
        "tool_started",
        "tool_completed",
    ]


def test_forbidden_tool_does_not_run():
    sandbox, tracer = create_restricted_sandbox()

    before = sandbox.get_state()

    with pytest.raises(
        ToolPermissionError,
        match="not permitted",
    ):
        sandbox.call_tool(
            "document.delete",
            document_id="important",
        )

    assert sandbox.get_state() == before

    assert not any(
        event.event_type == "tool_started"
        and event.tool_name == "document.delete"
        for event in tracer.events
    )

    assert any(
        event.event_type == "error"
        and event.tool_name == "document.delete"
        and event.payload["error_type"]
        == "ToolPermissionError"
        for event in tracer.events
    )