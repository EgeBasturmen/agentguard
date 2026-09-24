from agentguard.core.result import AssertionResult
from agentguard.core.scenario import Invariant


def evaluate_invariant(
    state: dict,
    invariant: Invariant,
) -> AssertionResult:

    if invariant.kind == "refund_total_lte_order_total":

        order_id = invariant.params["order_id"]

        order_total = state["orders"][order_id]["total"]

        refund_total = sum(
            refund["amount"]
            for refund in state["refunds"]
            if refund["order_id"] == order_id
        )

        passed = refund_total <= order_total

        return AssertionResult(
            name=f"invariant:{invariant.name}",
            passed=passed,
            message=(
                None
                if passed
                else (
                    f"Refund total {refund_total} exceeds "
                    f"order total {order_total}"
                )
            ),
        )

    return AssertionResult(
        name=f"invariant:{invariant.name}",
        passed=False,
        message=f"Unknown invariant kind: {invariant.kind}",
    )