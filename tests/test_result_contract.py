import pytest

from agentguard.contracts.compatibility import local_context
from agentguard.contracts.local import run_local_definition
from agentguard.contracts.models import ExecutionContext
from agentguard.contracts.results import summarize_result
from agentguard.core.outcome import effective_pass
from agentguard.regression.comparator import RegressionComparator
from examples.phase0 import FileAgent, sample_definitions, sandbox_factory


def test_existing_result_has_separate_execution_behavior_policy_cleanup(make_run):
    result = make_run()
    summary = summarize_result(result)
    assert summary.execution_mode == "observed"
    assert summary.execution_status == "completed"
    assert summary.behavior_status == "passed"
    assert summary.policy_status == "unknown"
    assert summary.evaluation_completeness == "complete"
    assert summary.cleanup_status == "not_required"
    assert summary.label == "PASS" and summary.gate_decision == "ALLOW"
    assert summary.coverage_gaps


def test_legacy_result_does_not_gain_an_assurance_claim(make_run):
    result = make_run()
    result.execution_context = None
    assert summarize_result(result).execution_mode == "unknown"


def test_legacy_error_does_not_gain_completed_execution(make_run):
    result = make_run()
    result.status, result.passed = "error", False
    result.error_stage = None
    assert summarize_result(result).execution_status == "unknown"


@pytest.mark.parametrize("change,label,behavior", [
    ("behavior", "FAIL", "failed"),
    ("evaluation", "ERROR", "not_evaluated"),
    ("cleanup", "INCONCLUSIVE", "passed"),
    ("missing", "INCONCLUSIVE", "not_evaluated"),
])
def test_errors_and_incomplete_evidence_are_not_behavior_success(make_run, change, label, behavior):
    result = make_run()
    if change == "behavior":
        result.assertions[0].passed = False
        result.passed, result.status = False, "failed"
    elif change == "evaluation":
        result.evaluation_status = "error"
    elif change == "cleanup":
        result.cleanup_status = "unverified"
    else:
        result.evaluation_status = "not_run"
    summary = summarize_result(result)
    assert summary.label == label
    assert summary.behavior_status == behavior
    assert summary.gate_decision == "BLOCK"
    assert summary.failure_reasons


def test_unsupported_capability_is_skipped_without_executing():
    definition = sample_definitions()["file-edit"]
    definition.required_capabilities.append("supports_cancellation")
    result = run_local_definition(definition, FileAgent(), sandbox_factory(definition))
    summary = summarize_result(result)
    assert summary.label == "SKIPPED"
    assert summary.execution_status == "not_started"
    assert summary.gate_decision == "BLOCK"


@pytest.mark.parametrize("claim", ["controlled", "capability", "adapter"])
def test_capability_metadata_cannot_upgrade_the_local_gate(make_run, claim):
    result = make_run()
    data = local_context().model_dump()
    if claim == "controlled":
        data["execution_mode"] = "controlled"
        for name in ("supports_isolation", "supports_tool_interception", "supports_authoritative_evidence"):
            data["capabilities"][name] = True
    elif claim == "capability":
        data["capabilities"]["supports_model_usage"] = True
    else:
        data["adapter"]["id"] = "unknown.backend"
    result.execution_context = ExecutionContext.model_validate(data)
    assert not effective_pass(result)
    assert summarize_result(result).gate_decision == "BLOCK"


def test_missing_or_different_context_cannot_be_compared_as_same_guarantee(make_run):
    before, after = make_run(), make_run()
    after.execution_context = None
    result = RegressionComparator().compare("before", before, "after", after)
    assert result.comparison_status == "incompatible"
    assert not result.passed
