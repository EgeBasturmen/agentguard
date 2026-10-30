
from copy import deepcopy
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
)

from agentguard.execution.tool_registry import ToolRegistry
from agentguard.sandbox.commerce import CommerceSandbox


class ToolBrokerError(RuntimeError):
    pass


class ToolNotAllowedError(ToolBrokerError):
    pass


class ToolArgumentsError(ToolBrokerError):
    pass


class ToolCallLimitExceeded(ToolBrokerError):
    pass


class GetOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: StrictStr = Field(min_length=1)


class RefundOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: StrictStr = Field(min_length=1)
    amount: float = Field(gt=0, allow_inf_nan=False)


class DeleteCustomerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: StrictStr = Field(min_length=1)


def create_commerce_registry(
    sandbox: CommerceSandbox,
) -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        "order.get",
        GetOrderArgs,
        sandbox.get_order,
    )

    registry.register(
        "payment.refund",
        RefundOrderArgs,
        sandbox.refund_order,
    )

    registry.register(
        "customer.delete",
        DeleteCustomerArgs,
        sandbox.delete_customer,
    )

    return registry


class ToolBroker:
    """
    Trusted tool dispatcher.

    Supports registered tools without requiring
    changes to the broker for each agent domain.
    """

    def __init__(
        self,
        sandbox: CommerceSandbox | None = None,
        max_tool_calls: int = 100,
        *,
        registry: ToolRegistry | None = None,
    ) -> None:
        if type(max_tool_calls) is not int or max_tool_calls < 1:
            raise ValueError(
                "max_tool_calls must be a positive integer."
            )

        if (sandbox is None) == (registry is None):
            raise ValueError(
                "Provide exactly one of sandbox or registry."
            )

        self._max_tool_calls = max_tool_calls
        self._call_count = 0

        self._registry = (
            registry
            if registry is not None
            else create_commerce_registry(sandbox)
        )

    @property
    def call_count(self) -> int:
        return self._call_count

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if self._call_count >= self._max_tool_calls:
            raise ToolCallLimitExceeded(
                "Maximum tool call limit exceeded."
            )

        # Count rejected attempts too.
        self._call_count += 1

        if not isinstance(tool_name, str):
            raise ToolNotAllowedError(
                "Tool name must be a string."
            )

        tool = self._registry.get(tool_name)

        if tool is None:
            raise ToolNotAllowedError(
                f"Tool is not allowed: {tool_name}"
            )

        try:
            validated = tool.schema.model_validate(arguments)
        except ValidationError as exc:
            raise ToolArgumentsError(
                f"Invalid arguments for {tool_name}: {exc}"
            ) from exc

        return deepcopy(
            tool.handler(**validated.model_dump())
        )
