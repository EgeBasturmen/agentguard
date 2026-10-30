
import pytest

from agentguard.execution.tool_broker import (
    ToolBroker,
    ToolNotAllowedError,
    ToolArgumentsError,
    ToolCallLimitExceeded,
)
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.tracing.collector import TraceCollector


@pytest.fixture
def broker():
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

    return ToolBroker(sandbox), sandbox


def test_get_order(broker):
    tool_broker, _ = broker

    result = tool_broker.execute(
        "order.get",
        {"order_id": "ORD-100"},
    )

    assert result["status"] == "paid"


def test_refund_order(broker):
    tool_broker, sandbox = broker

    result = tool_broker.execute(
        "payment.refund",
        {
            "order_id": "ORD-100",
            "amount": 50.0,
        },
    )

    assert result["amount"] == 50.0
    assert sandbox.get_state()["orders"]["ORD-100"]["status"] == "refunded"


def test_delete_customer(broker):
    tool_broker, sandbox = broker

    tool_broker.execute(
        "customer.delete",
        {"customer_id": "CUST-1"},
    )

    assert (
        sandbox.get_state()["customers"]["CUST-1"]["status"]
        == "deleted"
    )


def test_unknown_tool_is_rejected(broker):
    tool_broker, _ = broker

    with pytest.raises(ToolNotAllowedError):
        tool_broker.execute(
            "system.execute",
            {"command": "whoami"},
        )


def test_invalid_arguments_are_rejected(broker):
    tool_broker, _ = broker

    with pytest.raises(ToolArgumentsError):
        tool_broker.execute(
            "payment.refund",
            {
                "order_id": "ORD-100",
                "amount": -10,
            },
        )


def test_extra_arguments_are_rejected(broker):
    tool_broker, _ = broker

    with pytest.raises(ToolArgumentsError):
        tool_broker.execute(
            "order.get",
            {
                "order_id": "ORD-100",
                "admin": True,
            },
        )


def test_returned_order_cannot_mutate_state(broker):
    tool_broker, sandbox = broker

    result = tool_broker.execute(
        "order.get",
        {"order_id": "ORD-100"},
    )

    result["status"] = "deleted"

    assert sandbox.get_state()["orders"]["ORD-100"]["status"] == "paid"


def test_tool_call_limit_is_enforced(broker):
    _, sandbox = broker

    limited_broker = ToolBroker(
        sandbox,
        max_tool_calls=2,
    )

    limited_broker.execute(
        "order.get",
        {"order_id": "ORD-100"},
    )

    limited_broker.execute(
        "order.get",
        {"order_id": "ORD-100"},
    )

    assert limited_broker.call_count == 2

    with pytest.raises(ToolCallLimitExceeded):
        limited_broker.execute(
            "order.get",
            {"order_id": "ORD-100"},
        )


def test_rejected_calls_count_toward_limit(broker):
    _, sandbox = broker

    limited_broker = ToolBroker(
        sandbox,
        max_tool_calls=1,
    )

    with pytest.raises(ToolNotAllowedError):
        limited_broker.execute(
            "system.execute",
            {"command": "whoami"},
        )

    assert limited_broker.call_count == 1

    with pytest.raises(ToolCallLimitExceeded):
        limited_broker.execute(
            "order.get",
            {"order_id": "ORD-100"},
        )


def test_invalid_arguments_count_toward_limit(broker):
    _, sandbox = broker

    limited_broker = ToolBroker(
        sandbox,
        max_tool_calls=1,
    )

    with pytest.raises(ToolArgumentsError):
        limited_broker.execute(
            "payment.refund",
            {
                "order_id": "ORD-100",
                "amount": -50,
            },
        )

    assert limited_broker.call_count == 1

    with pytest.raises(ToolCallLimitExceeded):
        limited_broker.execute(
            "order.get",
            {"order_id": "ORD-100"},
        )


@pytest.mark.parametrize(
    "limit",
    [0, -1, True],
)
def test_invalid_call_limit_is_rejected(broker, limit):
    _, sandbox = broker

    with pytest.raises(ValueError):
        ToolBroker(
            sandbox,
            max_tool_calls=limit,
        )
