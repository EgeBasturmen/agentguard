"""Projection of existing RunResult; the existing effective_pass remains the gate."""

from typing import Literal

from pydantic import Field

from agentguard.contracts.models import VersionedContract
from agentguard.core.outcome import effective_pass
from agentguard.core.result import RunResult


class ResultSummary(VersionedContract):
    execution_mode: Literal["observed", "controlled", "unknown"]
    execution_status: Literal["not_started", "completed", "error", "cancelled", "timed_out", "unknown"]
    behavior_status: Literal["passed", "failed", "partial", "not_evaluated"]
    policy_status: Literal["passed", "violation", "not_evaluated", "unknown"] = "unknown"
    cleanup_status: Literal["not_required", "verified", "unverified"]
    evaluation_completeness: Literal["not_run", "complete", "error"]
    label: Literal["PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIPPED"]
    gate_decision: Literal["ALLOW", "BLOCK"]
    failure_reasons: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)


def summarize_result(result: RunResult) -> ResultSummary:
    accepted = effective_pass(result)
    checks = [item for item in result.assertions if item.name != "agent:execution"]
    if result.evaluation_status != "complete" or not checks:
        behavior = "not_evaluated"
    else:
        behavior = "passed" if all(item.passed for item in checks) else "failed"
    error = result.status == "error" or result.evaluation_status == "error"
    unsupported = result.error_type == "UnsupportedContractError"
    label = "SKIPPED" if unsupported else "PASS" if accepted else "ERROR" if error else "FAIL" if behavior == "failed" else "INCONCLUSIVE"
    reasons = [item.message or item.name for item in result.assertions if not item.passed]
    if result.error_message:
        reasons.append(result.error_message)
    if not accepted and not reasons:
        reasons.append("Execution, evaluation, evidence or cleanup is incomplete or inconsistent.")
    context = result.execution_context
    return ResultSummary(
        execution_mode=context.execution_mode if context else "unknown",
        policy_status=result.policy_status,
        execution_status="cancelled" if result.error_type == "ExecutionCancelled"
        else "timed_out" if result.error_type in {"ExecutionTimeout", "BuildTimeout", "ToolDeadlineError"}
        else "not_started" if result.error_stage == "validation"
        else "error" if result.error_stage in {"provision", "snapshot", "execution", "diff"}
        else "unknown" if result.status == "error" and result.error_stage is None
        else "completed" if result.evaluation_status in {"complete", "error"} else "unknown",
        behavior_status=behavior, cleanup_status=result.cleanup_status,
        evaluation_completeness=result.evaluation_status, label=label,
        gate_decision="ALLOW" if accepted else "BLOCK", failure_reasons=reasons,
        coverage_gaps=(list(context.limitations) if context else ["Execution assurance and capabilities were not recorded."]) + result.coverage_gaps,
    )
