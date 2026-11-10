
"""Centralized run outcome evaluation."""

from agentguard.core.result import RunResult
from agentguard.core.manifest import ComparisonManifest
from agentguard.contracts.compatibility import supported_matrix


def evidence_errors(result: RunResult) -> list[str]:
    """Shared evidence integrity checks for run and comparison decisions."""
    problems = []
    context = result.execution_context
    if context is not None:
        profile = next((row for row in supported_matrix()
                        if row.component == "agent" and row.adapter == context.adapter
                        and row.implementation == "implemented"), None)
        if profile is None or context.execution_mode not in profile.execution_modes:
            problems.append("Execution context claims an unsupported adapter or assurance mode.")
        elif any(not getattr(profile.capabilities, name) for name in context.capabilities.supported()):
            problems.append("Execution context claims unsupported capabilities.")
    names = [item.name for item in result.assertions]
    ids = [item.assertion_id for item in result.assertions if item.assertion_id is not None]
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        problems.append("Duplicate assertion identity.")
    paths = [item.path for item in result.mutations]
    if len(paths) != len(set(paths)):
        problems.append("Duplicate mutation paths.")
    if result.comparison_manifest is not None:
        try:
            manifest = ComparisonManifest.model_validate(result.comparison_manifest.model_dump())
            required, observed = set(manifest.check_definitions()), set(ids)
            if manifest.scenario.name != result.scenario_name:
                problems.append("Scenario name does not match the manifest.")
            if result.evaluation_status == "complete" and observed != required:
                problems.append("Completed evaluation does not contain exactly the manifest checks.")
            if observed - required:
                problems.append("Unexpected assertion identities.")
            if any(item.assertion_id is None and item.name != "agent:execution" for item in result.assertions):
                problems.append("Manifest assertion identity is missing.")
        except ValueError:
            problems.append("Comparison manifest is invalid or was modified.")
    return problems


def effective_pass(result: RunResult) -> bool:
    """
    Return True only when a run is safe to classify as PASS.

    Cleanup verification is required when cleanup is applicable.
    """
    return (
        result.passed is True
        and result.status == "passed"
        and result.evaluation_status == "complete"
        and result.error_type is None
        and result.error_message is None
        and result.error_stage is None
        and result.evaluation_error_type is None
        and result.evaluation_error_message is None
        and result.cleanup_status in {
            "not_required",
            "verified",
        }
        and bool(result.assertions)
        and all(assertion.passed for assertion in result.assertions)
        and not evidence_errors(result)
    )
