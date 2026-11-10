"""Report input and presentation adapters around the single comparison engine."""

from typing import Any

from agentguard.core.outcome import effective_pass
from agentguard.regression.comparator import RegressionComparator
from agentguard.regression.models import ComparisonResult
from agentguard.reporting.models import RunReport, load_report, validate_report


def compare_reports(
    baseline: RunReport | dict[str, Any],
    candidate: RunReport | dict[str, Any],
) -> ComparisonResult:
    base = validate_report(baseline)
    current = validate_report(candidate)
    return RegressionComparator().compare(base.agent, base.result, current.agent, current.result)


def comparison_view(result: ComparisonResult) -> dict[str, Any]:
    """Presentation only: all decisions and findings come from ComparisonResult."""
    def metrics(run):
        return {"assertions": len(run.assertions), "mutations": len(run.mutations), "traces": len(run.traces)}
    return {
        "scenario": result.baseline_run.scenario_name,
        "baseline_agent": result.baseline_name,
        "candidate_agent": result.candidate_name,
        "baseline_verdict": "PASS" if effective_pass(result.baseline_run) else "FAIL",
        "candidate_verdict": "PASS" if effective_pass(result.candidate_run) else "FAIL",
        "regression_detected": result.regression_detected,
        "comparison_status": result.comparison_status,
        "no_regression": result.no_regression,
        "gate_decision": result.gate_decision,
        "failure_reasons": result.failure_reasons,
        "assertion_regressions": [
            {"name": f.key, "message": f.message} for f in result.findings
            if f.finding_type == "new_assertion_failure"
        ],
        "assertion_improvements": [f.key for f in result.findings if f.finding_type == "resolved_assertion_failure"],
        "removed_assertions": [f.key for f in result.findings if f.finding_type == "removed_assertion"],
        "new_assertions": [f.key for f in result.findings if f.finding_type == "added_assertion"],
        "added_tools": [f.key for f in result.findings if f.finding_type == "new_tool_call"],
        "added_mutations": [m.model_dump(mode="json") for m in result.candidate_run.mutations
                            if any(f.finding_type == "new_state_mutation" and f.key == m.path for f in result.findings)],
        "baseline_metrics": metrics(result.baseline_run),
        "candidate_metrics": metrics(result.candidate_run),
    }
