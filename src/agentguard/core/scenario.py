from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, model_serializer
from agentguard.evaluation.specs import EvaluationSpec,Turn,Fault

from agentguard.core.paths import PathResolutionError, parse_path, resolve_path, values_equal


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NamedSpec(StrictSpec):
    @field_validator("name", "task", "path", "tool_name", "order_id", check_fields=False)
    @classmethod
    def reject_blank_strings(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            raise ValueError("Text fields must not be blank.")
        return value


class StatePathSpec(NamedSpec):
    path: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        parse_path(value)
        return value


class ExpectedState(StatePathSpec):
    value: Any


class ForbiddenChange(StatePathSpec):
    pass


CallCount = Annotated[int, Field(ge=0)]


class ToolExpectation(NamedSpec):
    tool_name: str
    times: CallCount | None = None
    max_times: CallCount | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> "ToolExpectation":
        if self.times is None and self.max_times is None:
            raise ValueError("Tool expectations require times or max_times.")
        if self.times is not None and self.max_times is not None and self.times > self.max_times:
            raise ValueError("times cannot exceed max_times.")
        return self


class RefundInvariantParams(NamedSpec):
    order_id: str


class Invariant(NamedSpec):
    name: str
    kind: Literal["refund_total_lte_order_total"]
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_params(self) -> "Invariant":
        self.params = RefundInvariantParams.model_validate(self.params).model_dump()
        return self


class Scenario(NamedSpec):
    name: str
    task: str
    generation: dict | None = None
    initial_state: dict[str, Any]
    expected_state: list[ExpectedState] = Field(default_factory=list)
    forbidden_changes: list[ForbiddenChange] = Field(default_factory=list)
    tool_expectations: list[ToolExpectation] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)
    evaluations: list[EvaluationSpec] = Field(default_factory=list,max_length=100)
    turns: list[Turn] = Field(default_factory=list,max_length=20)
    faults: list[Fault] = Field(default_factory=list,max_length=100)

    @model_serializer(mode='wrap')
    def compatible_dump(self,handler):
        data=handler(self)
        for key in ('evaluations','turns','faults'):
            if not getattr(self,key):data.pop(key,None)
        return data

    @model_validator(mode="after")
    def validate_checks(self) -> "Scenario":
        if not any((self.expected_state, self.forbidden_changes, self.tool_expectations, self.invariants,self.evaluations)):
            raise ValueError("Scenario requires at least one evaluation check.")
        groups = (
            [parse_path(check.path) for check in self.expected_state],
            [parse_path(check.path) for check in self.forbidden_changes],
            [check.tool_name for check in self.tool_expectations],
            [check.name for check in self.invariants],
            [check.id for check in self.evaluations],
            [check.id for check in self.faults],
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("Duplicate evaluation checks are not allowed.")
        if len({(f.tool_name,f.call_index) for f in self.faults})!=len(self.faults):raise ValueError('Overlapping injected faults')
        recovery={check.config['fault_id'] for check in self.evaluations if check.kind=='fault_recovery'}
        if recovery!={fault.id for fault in self.faults}:raise ValueError('Every declared fault requires an explicit recovery evaluator')
        if sum(len(turn.task.encode()) for turn in self.turns)>8192:raise ValueError('Session input budget exceeded')
        for index, parent in enumerate(self.expected_state):
            parent_parts = parse_path(parent.path)
            for child in self.expected_state[index + 1:]:
                child_parts = parse_path(child.path)
                ancestor, descendant = parent, child
                if len(parent_parts) > len(child_parts):
                    ancestor, descendant = child, parent
                prefix, parts = parse_path(ancestor.path), parse_path(descendant.path)
                if parts[:len(prefix)] == prefix:
                    try:
                        actual = resolve_path(ancestor.value, parts[len(prefix):])
                    except PathResolutionError as exc:
                        raise ValueError("Overlapping expected states contradict each other.") from exc
                    if not values_equal(actual, descendant.value):
                        raise ValueError("Overlapping expected states contradict each other.")
        for expected in self.expected_state:
            expected_parts = parse_path(expected.path)
            for forbidden in self.forbidden_changes:
                forbidden_parts = parse_path(forbidden.path)
                if expected_parts[:len(forbidden_parts)] == forbidden_parts:
                    initial_parts = expected_parts
                    target = expected.value
                elif forbidden_parts[:len(expected_parts)] == expected_parts:
                    initial_parts = forbidden_parts
                    try:
                        target = resolve_path(expected.value, forbidden_parts[len(expected_parts):])
                    except PathResolutionError:
                        try:
                            resolve_path(self.initial_state, forbidden_parts)
                        except PathResolutionError:
                            continue
                        raise ValueError("Expected state removes a forbidden path.")
                else:
                    continue
                try:
                    initial = resolve_path(self.initial_state, initial_parts)
                except PathResolutionError as exc:
                    raise ValueError("Expected state creates a forbidden path.") from exc
                if not values_equal(initial, target):
                    raise ValueError("Expected state contradicts a forbidden change.")
        return self
