import pytest

from agentguard.sandbox.generic import GenericSandbox
from agentguard.tracing.collector import TraceCollector

from examples.generic_tools import (
    read_document,
    delete_document,
)


def create_sandbox():
    tracer = TraceCollector()

    sandbox = GenericSandbox(
        initial_state={
            "documents": {
                "report": {
                    "content": "Monthly report",
                },
                "important": {
                    "content": "Do not delete",
                },
            }
        },
        tracer=tracer,
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


def test_registered_tool_can_read_document():
    sandbox, tracer = create_sandbox()

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

    assert tracer.events[0].tool_name == "document.read"


def test_registered_tool_can_change_state():
    sandbox, tracer = create_sandbox()

    sandbox.call_tool(
        "document.delete",
        document_id="important",
    )

    assert "important" not in (
        sandbox.get_state()["documents"]
    )

    assert tracer.events[0].tool_name == "document.delete"


def test_unknown_tool_is_rejected():
    sandbox, _ = create_sandbox()

    with pytest.raises(
        ValueError,
        match="not registered",
    ):
        sandbox.call_tool("document.unknown")


def test_duplicate_tool_registration_is_rejected():
    sandbox, _ = create_sandbox()

    with pytest.raises(
        ValueError,
        match="already registered",
    ):
        sandbox.register_tool(
            "document.read",
            read_document,
        )


def test_tool_error_is_traced():
    sandbox, tracer = create_sandbox()

    with pytest.raises(KeyError):
        sandbox.call_tool(
            "document.read",
            document_id="missing",
        )

    assert [
        event.event_type
        for event in tracer.events
    ] == [
        "tool_started",
        "error",
    ]

    assert tracer.events[-1].tool_name == "document.read"