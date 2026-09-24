from pydantic import BaseModel, Field
from typing import Literal
from agentguard.core.mutation import StateMutation
from agentguard.core.trace import TraceEvent



RunStatus= Literal["passed","failed","error"]

class AssertionResult(BaseModel):
    name: str
    passed: bool
    message: str | None = None


class RunResult(BaseModel):
    scenario_name: str

    passed: bool

    assertions: list[AssertionResult] = Field(
        default_factory=list
    )

    mutations: list[StateMutation] = Field(
        default_factory=list
    )

    traces: list[TraceEvent] = Field(
        default_factory=list
    )

    status: RunStatus = "passed"

    error_type: str | None = None
    error_message: str | None = None