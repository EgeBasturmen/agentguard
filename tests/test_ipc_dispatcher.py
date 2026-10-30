
import pytest

from agentguard.execution.ipc_dispatcher import IPCDispatcher
from agentguard.execution.ipc_protocol import (
    IPCProtocolError,
    ToolCallRequest,
    ToolCallResponse,
    decode_ipc_message,
    encode_ipc_message,
)
from agentguard.execution.tool_broker import ToolBroker
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.tracing.collector import TraceCollector


@pytest.fixture
def dispatcher():
    state = {
        "orders": {
            "ORD-100": {
                "status": "paid",
                "total": 100.0,
            }
        },
        "refunds": [],
        "customers": {
            "CUST-1": {
                "status": "active",
            }
        },
    }

    sandbox = CommerceSandbox(
        initial_state=state,
        tracer=TraceCollector(),
    )

    broker = ToolBroker(
        sandbox,
        max_tool_calls=3,
    )

    return IPCDispatcher(broker), sandbox, broker


def send(dispatcher, call_id, tool_name, arguments):
    request = ToolCallRequest(
        call_id=call_id,
        tool_name=tool_name,
        arguments=arguments,
    )

    response_bytes = dispatcher.dispatch(
        encode_ipc_message(request)
    )

    return decode_ipc_message(
        response_bytes,
        ToolCallResponse,
    )


def test_dispatch_get_order(dispatcher):
    ipc, _, broker = dispatcher

    response = send(
        ipc,
        "call-1",
        "order.get",
        {"order_id": "ORD-100"},
    )

    assert response.success is True
    assert response.result["status"] == "paid"
    assert broker.call_count == 1


def test_dispatch_refund_mutates_trusted_state(dispatcher):
    ipc, sandbox, _ = dispatcher

    response = send(
        ipc,
        "call-1",
        "payment.refund",
        {
            "order_id": "ORD-100",
            "amount": 50.0,
        },
    )

    assert response.success is True

    state = sandbox.get_state()

    assert state["orders"]["ORD-100"]["status"] == "refunded"
    assert len(state["refunds"]) == 1


def test_dispatch_unknown_tool_rejected(dispatcher):
    ipc, _, _ = dispatcher

    response = send(
        ipc,
        "call-1",
        "system.execute",
        {"command": "whoami"},
    )

    assert response.success is False
    assert response.error_code == "TOOL_NOT_ALLOWED"


def test_dispatch_invalid_arguments_rejected(dispatcher):
    ipc, _, _ = dispatcher

    response = send(
        ipc,
        "call-1",
        "payment.refund",
        {
            "order_id": "ORD-100",
            "amount": -100,
        },
    )

    assert response.success is False
    assert response.error_code == "INVALID_ARGUMENTS"


def test_duplicate_call_id_rejected(dispatcher):
    ipc, _, broker = dispatcher

    send(
        ipc,
        "call-1",
        "order.get",
        {"order_id": "ORD-100"},
    )

    with pytest.raises(IPCProtocolError):
        send(
            ipc,
            "call-1",
            "order.get",
            {"order_id": "ORD-100"},
        )

    assert broker.call_count == 1


def test_tool_call_limit_enforced(dispatcher):
    ipc, _, broker = dispatcher

    for number in range(3):
        response = send(
            ipc,
            f"call-{number}",
            "order.get",
            {"order_id": "ORD-100"},
        )
        assert response.success is True

    response = send(
        ipc,
        "call-4",
        "order.get",
        {"order_id": "ORD-100"},
    )

    assert response.success is False
    assert response.error_code == "TOOL_CALL_LIMIT_EXCEEDED"
    assert broker.call_count == 3


def test_malformed_json_rejected(dispatcher):
    ipc, _, _ = dispatcher

    with pytest.raises(IPCProtocolError):
        ipc.dispatch(b"{invalid-json}")


def test_internal_errors_are_not_exposed(dispatcher):
    ipc, _, _ = dispatcher

    response = send(
        ipc,
        "call-1",
        "order.get",
        {"order_id": "DOES-NOT-EXIST"},
    )

    assert response.success is False
    assert response.error_code == "TOOL_EXECUTION_ERROR"
