
import pytest
from pydantic import ValidationError

from agentguard.execution.ipc_protocol import (
    IPCProtocolError,
    MAX_IPC_MESSAGE_BYTES,
    ToolCallRequest,
    ToolCallResponse,
    decode_ipc_message,
    encode_ipc_message,
)


def test_tool_call_round_trip():
    request = ToolCallRequest(
        call_id="call-001",
        tool_name="order.get",
        arguments={"order_id": "ORD-100"},
    )

    encoded = encode_ipc_message(request)

    decoded = decode_ipc_message(
        encoded,
        ToolCallRequest,
    )

    assert decoded == request
    assert encoded.endswith(b"\n")


def test_tool_response_round_trip():
    response = ToolCallResponse(
        call_id="call-001",
        success=True,
        result={"status": "paid"},
    )

    decoded = decode_ipc_message(
        encode_ipc_message(response),
        ToolCallResponse,
    )

    assert decoded == response


def test_invalid_protocol_version_rejected():
    with pytest.raises(ValidationError):
        ToolCallRequest(
            protocol_version=999,
            call_id="call-001",
            tool_name="order.get",
            arguments={},
        )


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        ToolCallRequest(
            call_id="call-001",
            tool_name="order.get",
            arguments={},
            command="whoami",
        )


def test_invalid_call_id_rejected():
    with pytest.raises(ValidationError):
        ToolCallRequest(
            call_id="../escape",
            tool_name="order.get",
            arguments={},
        )


def test_oversized_message_rejected():
    request = ToolCallRequest(
        call_id="call-001",
        tool_name="order.get",
        arguments={
            "payload": "A" * MAX_IPC_MESSAGE_BYTES,
        },
    )

    with pytest.raises(IPCProtocolError):
        encode_ipc_message(request)


def test_invalid_json_rejected():
    with pytest.raises(IPCProtocolError):
        decode_ipc_message(
            b"{invalid-json}",
            ToolCallRequest,
        )


def test_unknown_tool_name_is_valid_protocol():
    request = ToolCallRequest(
        call_id="call-001",
        tool_name="system.execute",
        arguments={"command": "whoami"},
    )

    assert request.tool_name == "system.execute"
