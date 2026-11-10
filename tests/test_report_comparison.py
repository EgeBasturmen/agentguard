from agentguard.core.mutation import StateMutation
from agentguard.core.trace import TraceEvent
from agentguard.reporting.comparison import compare_reports, comparison_view
from agentguard.reporting.models import RunReport


def test_detects_behavioral_regression(make_run):
    baseline = make_run()
    candidate = make_run()
    candidate.assertions[0].passed = False
    candidate.passed = False
    candidate.status = "failed"
    candidate.mutations = [StateMutation(path="protected.flag", mutation_type="updated", before="keep", after="deleted")]
    candidate.traces.append(TraceEvent(event_type="tool_started", tool_name="resource.delete"))
    result = compare_reports(RunReport.from_result(baseline, "GoodAgent"), RunReport.from_result(candidate, "BadAgent"))
    view = comparison_view(result)
    assert result.regression_detected
    assert not result.passed
    assert len(view["assertion_regressions"]) == 1
    assert view["added_tools"] == ["resource.delete"]
    assert len(view["added_mutations"]) == 1


def test_rejects_different_scenarios(make_run):
    baseline = make_run()
    scenario = baseline.comparison_manifest.scenario.model_copy(deep=True)
    scenario.name = "different"
    result = compare_reports(RunReport.from_result(baseline, "A"), RunReport.from_result(make_run(scenario), "B"))
    assert result.comparison_status == "incompatible"
    assert result.gate_decision == "BLOCK"
