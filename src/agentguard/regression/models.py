from typing import Any, Literal

from pydantic import BaseModel, Field

from agentguard.core.result import RunResult


RegressionType = Literal[
    "new_assertion_failure",
    "resolved_assertion_failure",
    "new_tool_call",
    "removed_tool_call",
    "tool_call_count_changed",
    "new_state_mutation",
    "removed_state_mutation",
    "state_mutation_changed",
    "new_execution_error",
    "resolved_execution_error",
]


class RegressionFinding(BaseModel):
    finding_type: RegressionType
    key: str

    severity: Literal[
        "info",
        "warning",
        "error",
        "critical",
    ] = "error"

    message: str

    baseline: Any = None
    candidate: Any = None


class RegressionResult(BaseModel):
    baseline_name: str
    candidate_name: str

    baseline_run: RunResult
    candidate_run: RunResult

    findings: list[RegressionFinding] = Field(
        default_factory=list
    )

    blocking_regressions: int = 0
    passed: bool = True