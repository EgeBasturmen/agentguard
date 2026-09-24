from typing import Any

from pydantic import BaseModel, Field


class ExpectedState(BaseModel):
    path: str
    value: Any


class ForbiddenChange(BaseModel):
    path: str


class ToolExpectation(BaseModel):
    tool_name: str
    times: int | None = None
    max_times: int | None = None


class Invariant(BaseModel):
    name: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    name: str
    task: str

    initial_state: dict[str, Any]

    expected_state: list[ExpectedState] = Field(default_factory=list)
    forbidden_changes: list[ForbiddenChange] = Field(default_factory=list)
    tool_expectations: list[ToolExpectation] = Field(default_factory=list)
    invariants: list[Invariant] = Field(default_factory=list)