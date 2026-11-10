import json

import pytest

from agentguard.core.manifest import ComparisonManifest
from agentguard.core.mutation import StateMutation
from agentguard.core.result import AssertionResult
from agentguard.core.outcome import effective_pass
from agentguard.core.trace import TraceEvent
from agentguard.regression.comparator import RegressionComparator
from agentguard.reporting.comparison import compare_reports
from agentguard.reporting.dashboard import export_comparison_html
from agentguard.reporting.export import export_comparison_json, export_json
from agentguard.reporting.models import RunReport, load_report, validate_report


def compare(base, candidate):
    return RegressionComparator().compare("baseline", base, "candidate", candidate)


def payload(run):
    return RunReport.from_result(run, "agent").model_dump(mode="json")


def test_same_manifest_passes_across_direct_and_report_paths(make_run):
    base, candidate = make_run(), make_run()
    direct = compare(base, candidate)
    report = compare_reports(payload(base), payload(candidate))
    assert direct.model_dump() == report.model_copy(update={
        "baseline_name": "baseline", "candidate_name": "candidate",
    }).model_dump()
    assert direct.comparison_status == "verified"
    assert direct.no_regression and direct.passed
    assert direct.gate_decision == "ALLOW"


@pytest.mark.parametrize("change", ["task", "fixture", "threshold", "oracle", "environment", "evaluator"])
def test_fingerprint_detects_semantic_changes(make_run, change):
    base = make_run()
    scenario = base.comparison_manifest.scenario.model_copy(deep=True)
    if change == "task":
        scenario.task = "A different task"
    elif change == "fixture":
        scenario.initial_state["unused"] = "different fixture"
    elif change == "threshold":
        scenario.tool_expectations[0].max_times = 2
    elif change == "oracle":
        scenario.expected_state[0].value = 2
    candidate = make_run(scenario)
    if change in ("environment", "evaluator"):
        data = candidate.comparison_manifest.model_dump()
        data["fingerprint"] = None
        data["environment" if change == "environment" else "evaluator_version"] = "changed-v2"
        candidate.comparison_manifest = ComparisonManifest.model_validate(data)
    result = compare(base, candidate)
    assert result.comparison_status == "incompatible"
    assert not result.passed
    assert any(f.finding_type == "incompatible_runs" for f in result.findings)


def test_order_does_not_change_fingerprint_or_check_ids(make_run):
    scenario = make_run().comparison_manifest.scenario.model_copy(deep=True)
    scenario.initial_state["other"] = 2
    from agentguard.core.scenario import ExpectedState
    scenario.expected_state.append(ExpectedState(path="other", value=2))
    base = make_run(scenario)
    scenario = scenario.model_copy(deep=True)
    scenario.initial_state = dict(reversed(list(scenario.initial_state.items())))
    scenario.expected_state.reverse()
    candidate = make_run(scenario)
    assert base.comparison_manifest.fingerprint == candidate.comparison_manifest.fingerprint
    assert sorted(a.assertion_id for a in base.assertions) == sorted(a.assertion_id for a in candidate.assertions)
    candidate.assertions.reverse()
    assert compare(base, candidate).passed


def test_check_ids_stay_stable_when_a_threshold_changes(make_run):
    base = make_run()
    scenario = base.comparison_manifest.scenario.model_copy(deep=True)
    scenario.tool_expectations[0].max_times = 2
    candidate = make_run(scenario)
    assert [a.assertion_id for a in base.assertions] == [a.assertion_id for a in candidate.assertions]
    assert base.comparison_manifest.fingerprint != candidate.comparison_manifest.fingerprint


@pytest.mark.parametrize("remove_from_manifest", [False, True])
def test_removed_check_blocks_even_when_remaining_checks_pass(make_run, remove_from_manifest):
    base = make_run()
    if remove_from_manifest:
        scenario = base.comparison_manifest.scenario.model_copy(deep=True)
        scenario.tool_expectations.clear()
        candidate = make_run(scenario)
    else:
        candidate = make_run()
        candidate.assertions.pop()
    result = compare(base, candidate)
    assert not result.passed
    assert any(f.finding_type == "removed_assertion" for f in result.findings)
    assert result.regression_detected
    assert result.comparison_status != "verified"


@pytest.mark.parametrize("change", ["duplicate_name", "duplicate_id", "duplicate_mutation", "missing_id", "unknown_id"])
def test_ambiguous_evidence_cannot_pass(make_run, change):
    base, candidate = make_run(), make_run()
    if change == "duplicate_name":
        candidate.assertions.append(candidate.assertions[0].model_copy())
    elif change == "duplicate_id":
        candidate.assertions.append(candidate.assertions[0].model_copy(update={"name": "alias"}))
    elif change == "duplicate_mutation":
        candidate.mutations = [StateMutation(path="x", mutation_type="created", after=1)] * 2
    elif change == "missing_id":
        candidate.assertions[0].assertion_id = None
    else:
        candidate.assertions[0].assertion_id = "f" * 64
    result = compare(base, candidate)
    assert result.comparison_status == "unverified"
    assert not result.passed
    assert any(f.finding_type == "invalid_run_evidence" for f in result.findings)


def test_legacy_metadata_is_not_inferred(make_run):
    base, candidate = make_run(), make_run()
    base.comparison_manifest = candidate.comparison_manifest = None
    result = compare(base, candidate)
    assert result.comparison_status == "unverified"
    assert not result.no_regression
    assert not result.passed


def test_missing_manifest_checks_also_block_single_run_reports(make_run):
    run = make_run()
    run.assertions.pop()
    assert not effective_pass(run)
    assert RunReport.from_result(run, "Agent").verdict == "FAIL"


def test_changed_manifest_is_not_accepted_by_single_run_gate(make_run):
    run = make_run()
    run.comparison_manifest.scenario.task = "modified after execution"
    assert not effective_pass(run)


def test_identical_behavior_failure_is_not_release_accepted(make_run, tmp_path):
    base, candidate = make_run(), make_run()
    for run in (base, candidate):
        run.assertions[0].passed = False
        run.passed, run.status = False, "failed"
    result = compare(base, candidate)
    assert result.no_regression
    assert not result.regression_detected
    assert not result.passed
    assert result.failure_reasons
    html = export_comparison_html(result, tmp_path / "comparison.html").read_text(encoding="utf-8")
    assert "NO REGRESSION DETECTED" in html
    assert "BLOCK" in html


def test_identical_execution_errors_are_not_release_accepted(make_run):
    class Crash:
        def run(self, **kwargs):
            raise RuntimeError("crash")
    result = compare(make_run(agent=Crash()), make_run(agent=Crash()))
    assert result.no_regression
    assert result.gate_decision == "BLOCK"


def test_recovered_execution_error_can_pass(make_run):
    class Crash:
        def run(self, **kwargs):
            raise RuntimeError("crash")
    result = compare(make_run(agent=Crash()), make_run())
    assert result.passed
    assert any(f.finding_type == "resolved_execution_error" for f in result.findings)


@pytest.mark.parametrize("change", ["tool", "count", "new_mutation", "changed_mutation"])
def test_behavior_deltas_have_identical_decisions_everywhere(make_run, tmp_path, change):
    base, candidate = make_run(), make_run()
    if change in ("tool", "count"):
        if change == "count":
            base.traces.append(TraceEvent(event_type="tool_started", tool_name="resource.read"))
        candidate.traces.extend([TraceEvent(event_type="tool_started", tool_name="resource.read")] * 2)
    else:
        candidate.mutations = [StateMutation(path="x", mutation_type="updated", before=1, after=2)]
        if change == "changed_mutation":
            base.mutations = [StateMutation(path="x", mutation_type="updated", before=1, after=3)]
    direct = compare(base, candidate)
    via_reports = compare_reports(payload(base), payload(candidate))
    assert direct.gate_decision == via_reports.gate_decision == "BLOCK"
    assert direct.findings == via_reports.findings
    json_result = json.loads(export_comparison_json(via_reports, tmp_path / "comparison.json").read_text(encoding="utf-8"))
    html = export_comparison_html(via_reports, tmp_path / "comparison.html").read_text(encoding="utf-8")
    assert json_result["gate_decision"] == "BLOCK"
    assert json_result["passed"] is False
    assert "BLOCK" in html


def test_report_roundtrip_is_typed_and_preserves_fingerprint(make_run, tmp_path):
    run = make_run()
    path = export_json(run, tmp_path / "run.json", "Agent")
    report = load_report(path)
    assert isinstance(report, RunReport)
    assert report.schema_version == 2
    assert report.result.comparison_manifest.fingerprint == run.comparison_manifest.fingerprint


@pytest.mark.parametrize("version", [None, 0, 3, True, "2", 2.0])
def test_unsupported_schema_versions_are_rejected(make_run, version):
    data = payload(make_run())
    data["schema_version"] = version
    with pytest.raises(ValueError, match="schema version"):
        validate_report(data)


@pytest.mark.parametrize("change", ["verdict", "boolean", "extra", "nested_extra", "fingerprint", "manifest_body"])
def test_malformed_or_forged_reports_are_rejected(make_run, change):
    data = payload(make_run())
    if change == "verdict":
        data["verdict"] = "FAIL"
    elif change == "boolean":
        data["result"]["assertions"][0]["passed"] = "true"
    elif change == "extra":
        data["unexpected"] = 1
    elif change == "nested_extra":
        data["result"]["traces"][0]["unexpected"] = 1
    elif change == "fingerprint":
        data["result"]["comparison_manifest"]["fingerprint"] = "0" * 64
    else:
        data["result"]["comparison_manifest"]["scenario"]["task"] = "changed"
    with pytest.raises(ValueError):
        validate_report(data)


def test_forged_pass_for_error_or_cleanup_is_rejected(make_run):
    for changes in ({"status": "error"}, {"cleanup_status": "unverified"}, {"evaluation_status": "not_run"}):
        data = payload(make_run())
        data["result"].update(changes)
        with pytest.raises(ValueError, match="verdict"):
            validate_report(data)


def test_v1_migration_does_not_invent_manifest(make_run):
    data = payload(make_run())
    data["schema_version"] = 1
    data["result"].pop("comparison_manifest")
    for assertion in data["result"]["assertions"]:
        assertion.pop("assertion_id")
    migrated = validate_report(data)
    assert migrated.schema_version == 2
    assert migrated.result.comparison_manifest is None
    assert not compare_reports(migrated, migrated).passed


def test_v1_without_evaluation_evidence_cannot_claim_pass(make_run):
    data = payload(make_run())
    data["schema_version"] = 1
    data["result"].pop("evaluation_status")
    with pytest.raises(ValueError, match="legacy"):
        validate_report(data)


@pytest.mark.parametrize("raw", [
    '[]', '{"schema_version":2,"schema_version":1}', '{"result": NaN}',
    '{bad json}',
])
def test_report_loader_rejects_invalid_json(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        load_report(path)


def test_report_loader_bounds_input(tmp_path, monkeypatch):
    from agentguard.reporting import models
    monkeypatch.setattr(models, "MAX_REPORT_BYTES", 4)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 5)
    with pytest.raises(ValueError, match="size"):
        load_report(path)


def test_manifest_rejects_non_json_fixture_before_agent(make_run):
    scenario = make_run().comparison_manifest.scenario.model_copy(deep=True)
    scenario.initial_state["invalid"] = float("inf")
    result = make_run(scenario)
    assert result.status == "error"
    assert result.error_stage == "validation"


def test_serialized_manifest_must_include_a_hash(make_run):
    data = payload(make_run())
    data["result"]["comparison_manifest"].pop("fingerprint")
    with pytest.raises(ValueError, match="fingerprint"):
        validate_report(data)


def test_callable_sandbox_factory_and_explicit_environment_version(make_run):
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    scenario = make_run().comparison_manifest.scenario
    class Factory:
        def __call__(self, **kwargs):
            return GenericSandbox(**kwargs)
    class Agent:
        def run(self, **kwargs):
            return "done"
    baseline = ScenarioRunner(Factory(), environment_id="generic-v1").run(scenario, Agent())
    candidate = ScenarioRunner(Factory(), environment_id="generic-v2").run(scenario, Agent())
    assert baseline.passed and candidate.passed
    assert compare(baseline, candidate).comparison_status == "incompatible"


def test_dashboard_does_not_expand_placeholders_in_user_text(make_run, tmp_path):
    result = RegressionComparator().compare("__GATE_DECISION__", make_run(), "<script>alert(1)</script>", make_run())
    html = export_comparison_html(result, tmp_path / "comparison.html").read_text(encoding="utf-8")
    assert "__GATE_DECISION__" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>" not in html
