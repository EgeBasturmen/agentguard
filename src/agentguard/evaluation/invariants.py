import math

from agentguard.core.result import AssertionResult
from agentguard.core.scenario import Invariant


class InvariantEvaluationError(ValueError):
    """An invariant has insufficient or malformed fixture evidence."""


def _number(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise InvariantEvaluationError("Invariant amounts must be finite non-negative numbers.")
    return value


def evaluate_invariant(
    state: dict,
    invariant: Invariant,
) -> AssertionResult:

    if invariant.kind == "refund_total_lte_order_total":

        try:
            order_id = invariant.params["order_id"]
            order_total = _number(state["orders"][order_id]["total"])
            refunds = state["refunds"]
            if not isinstance(refunds, list):
                raise InvariantEvaluationError("Refund evidence must be a list.")
            refund_total = sum(
                _number(refund["amount"])
                for refund in refunds
                if refund["order_id"] == order_id
            )
            _number(refund_total)
        except (KeyError, TypeError, OverflowError) as exc:
            raise InvariantEvaluationError(f"Invalid invariant evidence: {exc}") from exc

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
