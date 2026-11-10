"""One typed comparison and release decision for all presentation surfaces."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from agentguard.core.outcome import effective_pass
from agentguard.core.result import RunResult


RegressionType = Literal[
    "new_assertion_failure", "resolved_assertion_failure", "removed_assertion",
    "added_assertion", "new_tool_call", "removed_tool_call",
    "tool_call_count_changed", "new_state_mutation", "removed_state_mutation",
    "state_mutation_changed", "new_execution_error", "resolved_execution_error",
    "candidate_run_not_verified", "comparison_not_verified", "incompatible_runs",
    "invalid_run_evidence",
]


class RegressionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    finding_type: RegressionType
    key: str
    severity: Literal["info", "warning", "error", "critical"] = "error"
    message: str
    baseline: Any = None
    candidate: Any = None


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    baseline_name: str
    candidate_name: str
    baseline_run: RunResult
    candidate_run: RunResult
    comparison_status: Literal["verified", "incompatible", "unverified"]
    findings: list[RegressionFinding] = Field(default_factory=list)

    @computed_field
    @property
    def blocking_regressions(self) -> int:
        return sum(f.severity in {"error", "critical"} for f in self.findings)

    @computed_field
    @property
    def regression_detected(self) -> bool:
        changes = {
            "new_assertion_failure", "removed_assertion", "new_tool_call",
            "tool_call_count_changed", "new_state_mutation", "state_mutation_changed",
        }
        return any(f.finding_type in changes for f in self.findings) or (
            self.candidate_run.status == "error" and self.baseline_run.status != "error"
        )

    @computed_field
    @property
    def no_regression(self) -> bool:
        return self.comparison_status == "verified" and not self.regression_detected

    @computed_field
    @property
    def passed(self) -> bool:
        return (
            self.comparison_status == "verified"
            and self.blocking_regressions == 0
            and effective_pass(self.candidate_run)
        )

    @computed_field
    @property
    def gate_decision(self) -> Literal["ALLOW", "BLOCK"]:
        return "ALLOW" if self.passed else "BLOCK"

    @computed_field
    @property
    def failure_reasons(self) -> list[str]:
        reasons = [f.message for f in self.findings if f.severity in {"error", "critical"}]
        if not effective_pass(self.candidate_run):
            reasons.append("Candidate run did not pass execution, evaluation and cleanup checks.")
        return reasons


# Keep the existing Python import path working.
RegressionResult = ComparisonResult
