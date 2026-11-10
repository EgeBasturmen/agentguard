from agentguard.core.mutation import StateMutation
from agentguard.core.trace import TraceEvent
from agentguard.regression.comparator import RegressionComparator
from agentguard.reporting.dashboard import export_comparison_html


def test_dashboard_exports_and_escapes_html(tmp_path, make_run):
    baseline, candidate = make_run(), make_run()
    candidate.assertions[0].passed = False
    candidate.assertions[0].message = "<Customer deleted>"
    candidate.status, candidate.passed = "failed", False
    candidate.mutations = [StateMutation(path="protected.flag", mutation_type="updated", before="keep", after="deleted")]
    candidate.traces.append(TraceEvent(event_type="tool_started", tool_name="resource.delete"))
    comparison = RegressionComparator().compare("GoodAgent", baseline, "<BadAgent>", candidate)
    path = export_comparison_html(comparison, tmp_path / "comparison.html")
    content = path.read_text(encoding="utf-8")
    assert "REGRESSION DETECTED" in content
    assert "BLOCK" in content
    assert "resource.delete" in content
    assert "protected.flag" in content
    assert "&lt;BadAgent&gt;" in content
    assert "<BadAgent>" not in content
