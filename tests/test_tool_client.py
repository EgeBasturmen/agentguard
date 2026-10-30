
import io
import pytest

from agentguard.execution.ipc_protocol import (
    ToolCallRequest,
    ToolCallResponse,
    decode_ipc_message,
    encode_ipc_message,
)
from agentguard.execution.tool_client import (
    ToolClient,
    ToolClientError,
)


def test_tool_client_get_order():
    reader = io.BytesIO(
        encode_ipc_message(
            ToolCallResponse(
                call_id="call-1",
                success=True,
                result={"status": "paid"},
            )
        )
    )

    writer = io.BytesIO()

    client = ToolClient(
        reader=reader,
        writer=writer,
    )

    result = client.get_order("ORD-100")

    assert result["status"] == "paid"

    writer.seek(0)

    request = decode_ipc_message(
        writer.readline(),
        ToolCallRequest,
    )

    assert request.tool_name == "order.get"
    assert request.arguments == {"order_id": "ORD-100"}


def test_tool_client_refund():
    reader = io.BytesIO(
        encode_ipc_message(
            ToolCallResponse(
                call_id="call-1",
                success=True,
                result={
                    "id": "REF-1",
                    "order_id": "ORD-100",
                    "amount": 50.0,
                },
            )
        )
    )

    client = ToolClient(
        reader=reader,
        writer=io.BytesIO(),
    )

    result = client.refund_order("ORD-100", 50.0)

    assert result["amount"] == 50.0


def test_tool_client_rejects_mismatched_call_id():
    reader = io.BytesIO(
        encode_ipc_message(
            ToolCallResponse(
                call_id="wrong-id",
                success=True,
                result={},
            )
        )
    )

    client = ToolClient(
        reader=reader,
        writer=io.BytesIO(),
    )

    with pytest.raises(ToolClientError):
        client.get_order("ORD-100")


def test_tool_client_rejects_failed_tool_call():
    reader = io.BytesIO(
        encode_ipc_message(
            ToolCallResponse(
                call_id="call-1",
                success=False,
                error_code="TOOL_NOT_ALLOWED",
            )
        )
    )

    client = ToolClient(
        reader=reader,
        writer=io.BytesIO(),
    )

    with pytest.raises(
        ToolClientError,
        match="TOOL_NOT_ALLOWED",
    ):
        client.call(
            "system.execute",
            {"command": "whoami"},
        )


def test_tool_client_rejects_closed_connection():
    client = ToolClient(
        reader=io.BytesIO(),
        writer=io.BytesIO(),
    )

    with pytest.raises(ToolClientError):
        client.get_order("ORD-100")
