from copy import deepcopy
from typing import Any

from agentguard.tracing.collector import TraceCollector


class CommerceSandbox:

    def __init__(
        self,
        initial_state: dict[str, Any],
        tracer: TraceCollector,
    ) -> None:

        self.state = deepcopy(initial_state)
        self.tracer = tracer

    def get_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def get_order(
        self,
        order_id: str,
    ) -> dict[str, Any]:

        self.tracer.emit(
            event_type="tool_started",
            tool_name="order.get",
            payload={
                "order_id": order_id
            },
        )

        order = deepcopy(
            self.state["orders"][order_id]
        )

        self.tracer.emit(
            event_type="tool_completed",
            tool_name="order.get",
            payload={
                "order_id": order_id,
                "result": order,
            },
        )

        return order

    def refund_order(
        self,
        order_id: str,
        amount: float,
    ) -> dict[str, Any]:

        self.tracer.emit(
            event_type="tool_started",
            tool_name="payment.refund",
            payload={
                "order_id": order_id,
                "amount": amount,
            },
        )

        order = self.state["orders"][order_id]

        refund_id = f"REF-{len(self.state['refunds']) + 1}"

        refund = {
            "id": refund_id,
            "order_id": order_id,
            "amount": amount,
        }

        self.state["refunds"].append(refund)

        order["status"] = "refunded"

        self.tracer.emit(
            event_type="tool_completed",
            tool_name="payment.refund",
            payload={
                "order_id": order_id,
                "amount": amount,
                "refund_id": refund_id,
            },
        )

        return deepcopy(refund)

    def delete_customer(
        self,
        customer_id: str,
    ) -> None:

        self.tracer.emit(
            event_type="tool_started",
            tool_name="customer.delete",
            payload={
                "customer_id": customer_id,
            },
        )

        customer = self.state["customers"][customer_id]

        customer["status"] = "deleted"

        self.tracer.emit(
            event_type="tool_completed",
            tool_name="customer.delete",
            payload={
                "customer_id": customer_id,
            },
        )