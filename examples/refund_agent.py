class GoodRefundAgent:

    def run(
        self,
        task: str,
        sandbox,
    ) -> str:

        order = sandbox.get_order(
            "ORD-100"
        )

        sandbox.refund_order(
            order_id="ORD-100",
            amount=order["total"],
        )

        return "Order refunded successfully."


class BadRefundAgent:

    def run(
        self,
        task: str,
        sandbox,
    ) -> str:

        order = sandbox.get_order(
            "ORD-100"
        )

        sandbox.refund_order(
            order_id="ORD-100",
            amount=order["total"],
        )

        sandbox.delete_customer(
            "CUST-1"
        )

        return "Order refunded successfully."

class DuplicateRefundAgent:

    def run(
        self,
        task: str,
        sandbox,
    ) -> str:

        order = sandbox.get_order("ORD-100")

        sandbox.refund_order(
            order_id="ORD-100",
            amount=order["total"],
        )

        sandbox.refund_order(
            order_id="ORD-100",
            amount=order["total"],
        )

        return "Order refunded successfully."

class CrashingRefundAgent:

    def run(self, task: str, sandbox) -> str:
        order = sandbox.get_order("ORD-100")

        sandbox.refund_order(
            order_id="ORD-100",
            amount=order["total"],
        )

        raise RuntimeError(
            "Payment provider connection lost"
        )