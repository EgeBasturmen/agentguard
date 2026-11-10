
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
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.records import AuditEvent
from agentguard.execution.transaction import invoke_transaction
from agentguard.evaluation.diff import diff_states
from agentguard.evaluation.assertions import evaluate_forbidden_change
from agentguard.tracing.collector import TraceCollector


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
        state: dict | None = None,
        policy: SecurityPolicy | None = None,
        forbidden_changes=None,
        tracer: TraceCollector | None = None,
        cancel_event=None,
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
        self._policy = policy.model_copy(deep=True) if policy is not None else None
        self._state = deepcopy(state or {})
        self.audit: list[AuditEvent] = []
        self.policy_violated = False
        self.runtime_error: Exception | None = None
        self._forbidden = forbidden_changes or []
        self._tracer = tracer or TraceCollector()
        self._cancel = cancel_event
        self.remaining_seconds: float | None = None

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
        if self._policy is not None:
            return self._execute_controlled(tool_name, arguments)
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

    @property
    def state(self):
        return deepcopy(self._state)

    def _execute_controlled(self, name, arguments):
        self._call_count += 1
        reason = None
        mutations = []
        status = "denied"
        try:
            tool = self._registry.get(name)
            if self._call_count > self._policy.max_tool_calls:
                reason = "TOOL_CALL_LIMIT_EXCEEDED"
            elif name not in self._policy.allowed_tools or tool is None:
                reason = "TOOL_NOT_ALLOWED"
            if reason:
                raise ToolNotAllowedError(reason)
            try:
                validated = tool.schema.model_validate(arguments, strict=True).model_dump()
            except ValidationError as exc:
                reason = "INVALID_ARGUMENTS"
                raise ToolArgumentsError(reason) from exc
            for field, values in self._policy.resource_scopes.get(name, {}).items():
                if field not in validated or not isinstance(validated[field], str) or validated[field] not in values:
                    reason = "RESOURCE_SCOPE_DENIED"
                    raise ToolNotAllowedError(reason)
            if not tool.stateful:
                raise RuntimeError("Controlled tools require a transactional state handler.")
            self._tracer.emit("tool_started", tool_name=name, payload={"arguments": deepcopy(validated)})
            limit = self._policy.tool_timeout_seconds
            if self.remaining_seconds is not None:
                limit = min(limit, self.remaining_seconds)
            result, proposed = invoke_transaction(tool.handler, self._state, validated, limit, self._cancel)
            mutations = diff_states(self._state, proposed)
            if any(not evaluate_forbidden_change(mutations, check).passed for check in self._forbidden):
                reason = "FORBIDDEN_EFFECT"
                raise ToolNotAllowedError(reason)
            self._state = proposed
            status = "completed"
            self._tracer.emit("tool_completed", tool_name=name, payload={"result": deepcopy(result)})
            return deepcopy(result)
        except (ToolNotAllowedError, ToolArgumentsError):
            self.policy_violated = True
            self._tracer.emit("error", tool_name=name, payload={"policy_violation": reason})
            raise
        except Exception as exc:
            status = "error"
            reason = type(exc).__name__
            self.runtime_error = exc
            raise
        finally:
            self.audit.append(AuditEvent(sequence=self._call_count, tool_name=name,
                arguments=deepcopy(arguments), status=status, reason=reason, mutations=mutations))
